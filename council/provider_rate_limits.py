from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pexpect


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
PROBE_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class ProviderRateLimitEntry:
    window: str
    percent_type: str
    percent_value: int
    reset_at: str | None = None


@dataclass(frozen=True)
class ProviderRateLimitProbeResult:
    binary: str
    status: str
    summary: str
    entries: tuple[ProviderRateLimitEntry, ...]
    source: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class _CommandAttemptResult:
    command: tuple[str, ...]
    return_code: int | None
    output: str
    timed_out: bool = False
    error: str | None = None


def probe_provider_rate_limits(
    binaries: Iterable[str],
    *,
    timeout_seconds: int = PROBE_TIMEOUT_SECONDS,
) -> dict[str, ProviderRateLimitProbeResult]:
    results: dict[str, ProviderRateLimitProbeResult] = {}

    for binary in sorted(set(binaries)):
        if binary == "codex":
            results[binary] = _probe_codex(timeout_seconds=timeout_seconds)
            continue
        if binary == "claude":
            results[binary] = _probe_claude(timeout_seconds=timeout_seconds)
            continue
        if binary == "gemini":
            results[binary] = _probe_gemini(timeout_seconds=timeout_seconds)
            continue

        results[binary] = ProviderRateLimitProbeResult(
            binary=binary,
            status="unsupported",
            summary="não suportado para probe automático",
            entries=(),
            source=None,
        )

    return results


def _probe_codex(*, timeout_seconds: int) -> ProviderRateLimitProbeResult:
    with tempfile.NamedTemporaryFile(prefix="council-codex-status-", suffix=".txt") as output_file:
        command = [
            "codex",
            "exec",
            "/status",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            output_file.name,
        ]
        attempt = _run_probe_command(command, timeout_seconds=timeout_seconds)
        output = _merge_outputs(attempt, Path(output_file.name).read_text(encoding="utf-8", errors="replace"))

    model = _extract_model_from_output(output)
    entries = _parse_codex_entries(output)
    if entries:
        return ProviderRateLimitProbeResult(
            binary="codex",
            status="ok",
            summary=_entries_summary(entries),
            entries=entries,
            source=_format_command(attempt.command),
            model=model,
        )

    return ProviderRateLimitProbeResult(
        binary="codex",
        status="unavailable",
        summary=f"indisponível automaticamente; use /status no Codex ({_attempt_reason(attempt)})",
        entries=(),
        source=_format_command(attempt.command),
        model=model,
    )


def _probe_claude(*, timeout_seconds: int) -> ProviderRateLimitProbeResult:
    cli_model = _probe_claude_model(timeout_seconds=timeout_seconds)
    candidates = ["/usage", "/cost"]
    last_attempt: _CommandAttemptResult | None = None

    for slash_command in candidates:
        attempt = _run_claude_repl_command(slash_command, timeout_seconds=timeout_seconds)
        last_attempt = attempt
        entries = _parse_claude_entries(attempt.output)
        if entries:
            return ProviderRateLimitProbeResult(
                binary="claude",
                status="ok",
                summary=_entries_summary(entries),
                entries=entries,
                source=_format_command(attempt.command),
                model=_extract_model_from_output(attempt.output) or cli_model,
            )

    return ProviderRateLimitProbeResult(
        binary="claude",
        status="unavailable",
        summary=(
            "indisponível automaticamente; use /usage no Claude "
            f"({_attempt_reason(last_attempt)})"
        ),
        entries=(),
        source=_format_command(last_attempt.command) if last_attempt is not None else None,
        model=cli_model,
    )


def _probe_gemini(*, timeout_seconds: int) -> ProviderRateLimitProbeResult:
    about_attempt = _run_gemini_repl_command("/about", timeout_seconds=timeout_seconds)
    about_output = about_attempt.output
    about_model = _extract_model_from_output(about_output)
    about_tier = _extract_tier_from_output(about_output)
    if about_model is None or about_tier is None:
        about_probe_attempt = _run_probe_command(
            ["gemini", "-p", "/about", "--output-format", "text"],
            timeout_seconds=timeout_seconds,
        )
        about_output = _join_outputs(about_output, about_probe_attempt.output)
        if about_model is None:
            about_model = _extract_model_from_output(about_output)
        if about_tier is None:
            about_tier = _extract_tier_from_output(about_output)
    about_entries = _parse_generic_entries(about_output)
    if about_entries:
        return ProviderRateLimitProbeResult(
            binary="gemini",
            status="ok",
            summary=_entries_summary(about_entries),
            entries=about_entries,
            source=_format_command(about_attempt.command),
            model=about_model,
        )

    stats_attempt = _run_gemini_repl_command("/stats", timeout_seconds=timeout_seconds)
    stats_entries = _parse_generic_entries(stats_attempt.output)
    if stats_entries:
        return ProviderRateLimitProbeResult(
            binary="gemini",
            status="ok",
            summary=_entries_summary(stats_entries),
            entries=stats_entries,
            source=_format_command(stats_attempt.command),
            model=_extract_model_from_output(stats_attempt.output) or about_model,
        )

    about_details: list[str] = []
    if about_model:
        about_details.append(f"modelo {about_model}")
    if about_tier:
        about_details.append(f"tier {about_tier}")
    about_suffix = f"; /about: {', '.join(about_details)}" if about_details else ""
    source_attempt = stats_attempt
    return ProviderRateLimitProbeResult(
        binary="gemini",
        status="unavailable",
        summary=(
            "sem indicador de cota restante via CLI; use /stats para uso da sessão "
            f"({_attempt_reason(stats_attempt)}){about_suffix}"
        ),
        entries=(),
        source=_format_command(source_attempt.command) if source_attempt is not None else None,
        model=about_model,
    )


def _probe_claude_model(*, timeout_seconds: int) -> str | None:
    for command in (
        ["claude", "-p", "model"],
        ["claude", "-p", "/model"],
    ):
        attempt = _run_probe_command(command, timeout_seconds=timeout_seconds)
        model = _extract_model_from_output(attempt.output)
        if model:
            return model
    return None


def _run_gemini_repl_command(slash_command: str, *, timeout_seconds: int) -> _CommandAttemptResult:
    return _run_interactive_repl_command(
        binary="gemini",
        slash_command=slash_command,
        timeout_seconds=timeout_seconds,
        ready_patterns=(r">", r"\$"),
        ready_timeout=15,
        response_patterns=(r">\s*$",),
        response_timeout=10,
    )


def _run_claude_repl_command(slash_command: str, *, timeout_seconds: int) -> _CommandAttemptResult:
    return _run_interactive_repl_command(
        binary="claude",
        slash_command=slash_command,
        timeout_seconds=timeout_seconds,
        ready_patterns=(r">\s*$", r"\$\s*$"),
        ready_timeout=20,
        response_patterns=(r">\s*$",),
        response_timeout=15,
    )


def _run_interactive_repl_command(
    *,
    binary: str,
    slash_command: str,
    timeout_seconds: int,
    ready_patterns: tuple[str, ...],
    ready_timeout: int,
    response_patterns: tuple[str, ...],
    response_timeout: int,
) -> _CommandAttemptResult:
    child: pexpect.spawn | None = None
    command = (binary,)
    try:
        child = pexpect.spawn(
            binary,
            timeout=timeout_seconds,
            encoding="utf-8",
        )
        ready_index = child.expect(
            [*ready_patterns, pexpect.TIMEOUT],
            timeout=min(ready_timeout, timeout_seconds),
        )
        if ready_index == len(ready_patterns):
            output = _strip_ansi(child.before or "")
            _terminate_repl_child(child)
            return _CommandAttemptResult(
                command=command,
                return_code=None,
                output=output,
                timed_out=True,
                error="timeout",
            )

        child.sendline(slash_command)
        response_index = child.expect(
            [*response_patterns, pexpect.TIMEOUT],
            timeout=min(response_timeout, timeout_seconds),
        )
        output = _strip_ansi(child.before or "")

        if response_index == len(response_patterns):
            _terminate_repl_child(child)
            return _CommandAttemptResult(
                command=(*command, slash_command),
                return_code=None,
                output=output,
                timed_out=True,
                error="timeout",
            )

        _terminate_repl_child(child)
        return _CommandAttemptResult(
            command=(*command, slash_command),
            return_code=child.exitstatus if child.exitstatus is not None else 0,
            output=output,
            timed_out=False,
            error=None,
        )
    except pexpect.exceptions.ExceptionPexpect as exc:
        if child is not None:
            _terminate_repl_child(child)
        return _CommandAttemptResult(
            command=command,
            return_code=None,
            output="",
            timed_out=False,
            error=str(exc),
        )
    except OSError as exc:
        if child is not None:
            _terminate_repl_child(child)
        return _CommandAttemptResult(
            command=command,
            return_code=None,
            output="",
            timed_out=False,
            error=str(exc),
        )


def _terminate_repl_child(child: pexpect.spawn) -> None:
    try:
        child.sendline("/exit")
    except Exception:
        pass
    try:
        child.expect(pexpect.EOF, timeout=5)
    except Exception:
        pass
    try:
        child.close()
    except Exception:
        try:
            child.close(force=True)
        except Exception:
            pass


def _run_probe_command(command: list[str], *, timeout_seconds: int) -> _CommandAttemptResult:
    # Uses a real PTY; required by CLIs that gate output on isatty() (Unix/Linux only).
    child: pexpect.spawn | None = None
    try:
        child = pexpect.spawn(
            command[0],
            args=command[1:],
            timeout=timeout_seconds,
            encoding="utf-8",
        )
        child.expect(pexpect.EOF)
        output = child.before or ""
        child.close()
        return _CommandAttemptResult(
            command=tuple(command),
            return_code=child.exitstatus,
            output=_strip_ansi(output),
            timed_out=False,
            error=None,
        )
    except pexpect.TIMEOUT:
        output = child.before if child is not None else ""
        if child is not None:
            try:
                child.close(force=True)
            except Exception:
                pass
        return _CommandAttemptResult(
            command=tuple(command),
            return_code=None,
            output=_strip_ansi(output),
            timed_out=True,
            error="timeout",
        )
    except pexpect.exceptions.ExceptionPexpect as exc:
        if child is not None:
            try:
                child.close(force=True)
            except Exception:
                pass
        return _CommandAttemptResult(
            command=tuple(command),
            return_code=None,
            output="",
            timed_out=False,
            error=str(exc),
        )
    except OSError as exc:
        return _CommandAttemptResult(
            command=tuple(command),
            return_code=None,
            output="",
            timed_out=False,
            error=str(exc),
        )


def _merge_outputs(attempt: _CommandAttemptResult, output_file_text: str) -> str:
    return _join_outputs(output_file_text, attempt.output)


def _join_outputs(*parts: str) -> str:
    return _strip_ansi("\n".join(part for part in parts if part))


def _parse_codex_entries(raw_output: str) -> tuple[ProviderRateLimitEntry, ...]:
    entries: list[ProviderRateLimitEntry] = []
    for match in re.finditer(r"(?im)^(?P<window>5h|weekly)\s+limit:\s*(?P<body>.+)$", raw_output):
        body = match.group("body")
        percent_match = re.search(r"(?P<value>\d{1,3})%\s+left", body, re.IGNORECASE)
        if percent_match is None:
            continue
        reset_match = re.search(r"resets?\s*(?P<reset>[^)\n]+)", body, re.IGNORECASE)
        entries.append(
            ProviderRateLimitEntry(
                window=match.group("window").lower(),
                percent_type="left",
                percent_value=int(percent_match.group("value")),
                reset_at=reset_match.group("reset").strip() if reset_match else None,
            )
        )

    return _dedupe_entries(entries)


def _parse_claude_entries(raw_output: str) -> tuple[ProviderRateLimitEntry, ...]:
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    entries: list[ProviderRateLimitEntry] = []
    window_patterns = (
        (re.compile(r"^Current session$", re.IGNORECASE), "current session"),
        (re.compile(r"^Current week(?:\s+\(all models\))?$", re.IGNORECASE), "current week"),
    )

    for index, line in enumerate(lines):
        for pattern, normalized_window in window_patterns:
            if pattern.match(line) is None:
                continue

            percent_value: int | None = None
            reset_at: str | None = None
            for cursor in range(index + 1, min(index + 7, len(lines))):
                used_match = re.search(r"(\d{1,3})%\s*used", lines[cursor], re.IGNORECASE)
                if used_match and percent_value is None:
                    percent_value = int(used_match.group(1))
                if lines[cursor].lower().startswith("resets"):
                    reset_at = lines[cursor][len("Resets"):].strip()
                if percent_value is not None and reset_at is not None:
                    break

            if percent_value is None:
                continue

            entries.append(
                ProviderRateLimitEntry(
                    window=normalized_window,
                    percent_type="used",
                    percent_value=percent_value,
                    reset_at=reset_at,
                )
            )

    return _dedupe_entries(entries)


def _parse_generic_entries(raw_output: str) -> tuple[ProviderRateLimitEntry, ...]:
    entries: list[ProviderRateLimitEntry] = []
    for match in re.finditer(
        (
            r"(?im)^(?P<window>(?:5h|weekly|current session|current week)[^\n:]*?)"
            r"(?:\s+limit)?\s*[:\-]?\s*"
            r"(?P<body>[^\n]+)$"
        ),
        raw_output,
    ):
        body = match.group("body")
        used_match = re.search(r"(\d{1,3})%\s*used", body, re.IGNORECASE)
        left_match = re.search(r"(\d{1,3})%\s*left", body, re.IGNORECASE)

        if used_match is None and left_match is None:
            continue

        percent_type = "used" if used_match is not None else "left"
        percent_value = int((used_match or left_match).group(1))
        reset_match = re.search(r"resets?\s*(?P<reset>[^)\n]+)", body, re.IGNORECASE)
        entries.append(
            ProviderRateLimitEntry(
                window=match.group("window").strip().lower(),
                percent_type=percent_type,
                percent_value=percent_value,
                reset_at=reset_match.group("reset").strip() if reset_match else None,
            )
        )

    return _dedupe_entries(entries)


def _dedupe_entries(entries: list[ProviderRateLimitEntry]) -> tuple[ProviderRateLimitEntry, ...]:
    unique: list[ProviderRateLimitEntry] = []
    seen: set[tuple[str, str, int, str | None]] = set()
    for entry in entries:
        key = (entry.window, entry.percent_type, entry.percent_value, entry.reset_at)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return tuple(unique)


def _entries_summary(entries: tuple[ProviderRateLimitEntry, ...]) -> str:
    parts: list[str] = []
    for entry in entries:
        chunk = f"{entry.window}: {entry.percent_value}% {entry.percent_type}"
        if entry.reset_at:
            chunk += f" (reset {entry.reset_at})"
        parts.append(chunk)
    return "; ".join(parts)


def _attempt_reason(attempt: _CommandAttemptResult | None) -> str:
    if attempt is None:
        return "nenhuma tentativa executada"
    if attempt.timed_out:
        return "timeout"
    if attempt.error:
        return attempt.error
    if attempt.return_code is None:
        return "falha não identificada"
    if attempt.return_code == 0:
        return "sem dados de cota na saída"
    return f"exit code {attempt.return_code}"


def _format_command(command: tuple[str, ...]) -> str:
    return " ".join(command)


def _extract_model_from_output(raw_output: str) -> str | None:
    labeled_value = _extract_labeled_value(raw_output, "Model")
    if labeled_value:
        return _normalize_model_value(labeled_value)

    for pattern in (
        re.compile(r"(?im)\bmodel\s*id\b(?:\s*(?:is|=|:)\s*)?`?(?P<value>[a-z0-9._-]+)`?"),
        re.compile(r"(?im)\bmodel\b\s*:\s*`?(?P<value>[a-z0-9._-]+)`?"),
        re.compile(r"(?im)`(?P<value>(?:claude|gpt|gemini)-[a-z0-9._-]+)`"),
    ):
        match = pattern.search(raw_output)
        if match is None:
            continue
        value = match.group("value").strip()
        if value:
            return _normalize_model_value(value)

    return None


def _extract_tier_from_output(raw_output: str) -> str | None:
    return _extract_labeled_value(raw_output, "Tier")


def _extract_labeled_value(raw_output: str, label: str) -> str | None:
    patterns = (
        re.compile(rf"(?im)^\s*{re.escape(label)}\s*:\s*(?P<value>.+?)\s*$"),
        re.compile(rf"(?im)^\s*{re.escape(label)}\s{{2,}}(?P<value>.+?)\s*$"),
    )
    for pattern in patterns:
        match = pattern.search(raw_output)
        if match is None:
            continue
        value = match.group("value").strip()
        if value:
            return value
    return None


def _normalize_model_value(value: str) -> str:
    normalized = value.strip()
    normalized = re.sub(r"\s+/model\b.*$", "", normalized, flags=re.IGNORECASE).strip()
    return normalized


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)
