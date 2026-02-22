from unittest.mock import Mock, patch

import pexpect
import council.provider_rate_limits as provider_limits


def test_parse_codex_entries_extracts_left_percentage_and_reset() -> None:
    output = """
5h limit: [█████-----] 70% left (resets 14:56)
Weekly limit: [██--------] 27% left (resets 04:07 on 26 Feb)
"""
    entries = provider_limits._parse_codex_entries(output)

    assert len(entries) == 2
    assert entries[0].window == "5h"
    assert entries[0].percent_type == "left"
    assert entries[0].percent_value == 70
    assert entries[0].reset_at == "14:56"
    assert entries[1].window == "weekly"
    assert entries[1].percent_value == 27


def test_parse_claude_entries_extracts_used_percentage_and_reset() -> None:
    output = """
Current session
2% used
Resets 7pm (America/Sao_Paulo)

Current week (all models)
29% used
Resets Feb 26, 2pm (America/Sao_Paulo)
"""
    entries = provider_limits._parse_claude_entries(output)

    assert len(entries) == 2
    assert entries[0].window == "current session"
    assert entries[0].percent_type == "used"
    assert entries[0].percent_value == 2
    assert entries[0].reset_at == "7pm (America/Sao_Paulo)"
    assert entries[1].window == "current week"
    assert entries[1].percent_value == 29


def test_extract_model_and_tier_from_output_supports_colon_and_table_formats() -> None:
    codex_like_output = "Model: gpt-5.3-codex (reasoning xhigh)\n"
    gemini_about_output = (
        "Model           Auto (Gemini 3)\n"
        "Tier            Gemini Code Assist in Google One AI Pro\n"
    )

    assert provider_limits._extract_model_from_output(codex_like_output) == "gpt-5.3-codex (reasoning xhigh)"
    assert provider_limits._extract_model_from_output(gemini_about_output) == "Auto (Gemini 3)"
    assert (
        provider_limits._extract_tier_from_output(gemini_about_output)
        == "Gemini Code Assist in Google One AI Pro"
    )


def test_probe_gemini_uses_about_as_fallback_for_model_and_tier(monkeypatch) -> None:
    attempts = iter(
        [
            provider_limits._CommandAttemptResult(
                command=("gemini", "-p", "/about", "--output-format", "text"),
                return_code=0,
                output=(
                    "Model           Auto (Gemini 3)\n"
                    "Tier            Gemini Code Assist in Google One AI Pro\n"
                ),
                timed_out=False,
                error=None,
            ),
            provider_limits._CommandAttemptResult(
                command=("gemini", "-p", "/stats", "--output-format", "text"),
                return_code=0,
                output="No quota information found",
                timed_out=False,
                error=None,
            ),
            provider_limits._CommandAttemptResult(
                command=("gemini", "-p", "/usage", "--output-format", "text"),
                return_code=0,
                output="No quota information found",
                timed_out=False,
                error=None,
            ),
            provider_limits._CommandAttemptResult(
                command=("gemini", "-p", "/status", "--output-format", "text"),
                return_code=0,
                output="No quota information found",
                timed_out=False,
                error=None,
            ),
        ]
    )

    monkeypatch.setattr(
        provider_limits,
        "_run_probe_command",
        lambda _command, timeout_seconds: next(attempts),
    )

    result = provider_limits._probe_gemini(timeout_seconds=1)

    assert result.status == "unavailable"
    assert result.model == "Auto (Gemini 3)"
    assert "tier Gemini Code Assist in Google One AI Pro" in result.summary


def test_run_probe_command_uses_pexpect_and_returns_output() -> None:
    child = Mock()
    child.before = "prefix "
    child.read.return_value = "\x1b[32mok\x1b[0m"
    child.exitstatus = 0

    with patch("pexpect.spawn", return_value=child) as spawn_mock:
        result = provider_limits._run_probe_command(["codex", "exec", "/status"], timeout_seconds=2)

    spawn_mock.assert_called_once_with(
        "codex",
        args=["exec", "/status"],
        timeout=2,
        encoding="utf-8",
    )
    child.expect.assert_called_once_with(pexpect.EOF)
    child.read.assert_called_once()
    child.close.assert_called_once_with()
    assert result.return_code == 0
    assert result.timed_out is False
    assert result.error is None
    assert result.output == "prefix ok"


def test_run_probe_command_handles_timeout() -> None:
    child = Mock()
    child.before = "partial \x1b[31moutput\x1b[0m"
    child.expect.side_effect = pexpect.TIMEOUT("timed out")

    with patch("pexpect.spawn", return_value=child):
        result = provider_limits._run_probe_command(["codex", "exec", "/status"], timeout_seconds=1)

    child.close.assert_called_once_with(force=True)
    assert result.return_code is None
    assert result.timed_out is True
    assert result.error == "timeout"
    assert result.output == "partial output"


def test_run_probe_command_handles_missing_binary() -> None:
    with patch(
        "pexpect.spawn",
        side_effect=pexpect.exceptions.ExceptionPexpect("The command was not found or was not executable: codex."),
    ):
        result = provider_limits._run_probe_command(["codex", "exec", "/status"], timeout_seconds=1)

    assert result.return_code is None
    assert result.timed_out is False
    assert "not found" in (result.error or "").lower()
    assert result.output == ""


def test_run_probe_command_preserves_nonzero_exit_code() -> None:
    child = Mock()
    child.before = ""
    child.read.return_value = "invalid request"
    child.exitstatus = 2

    with patch("pexpect.spawn", return_value=child):
        result = provider_limits._run_probe_command(["gemini", "-p", "/status"], timeout_seconds=2)

    assert result.return_code == 2
    assert result.timed_out is False
    assert result.error is None
    assert result.output == "invalid request"
