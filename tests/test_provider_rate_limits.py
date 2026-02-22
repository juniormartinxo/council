from unittest.mock import Mock, patch

import pexpect
import council.provider_rate_limits as provider_limits


def _build_interactive_child(expect_steps: list[tuple[int, str]], *, exitstatus: int = 0) -> Mock:
    child = Mock()
    child.before = ""
    child.exitstatus = exitstatus
    steps = iter(expect_steps)

    def _expect(_patterns, timeout=None):
        index, before = next(steps)
        child.before = before
        return index

    child.expect.side_effect = _expect
    return child


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


def test_probe_gemini_uses_about_as_fallback_for_model_and_tier() -> None:
    about_child = _build_interactive_child(
        [
            (0, ""),
            (
                0,
                (
                    "Model           Auto (Gemini 3)\n"
                    "Tier            Gemini Code Assist in Google One AI Pro\n"
                ),
            ),
            (0, ""),
        ]
    )
    stats_child = _build_interactive_child(
        [
            (0, ""),
            (0, "No quota information found"),
            (0, ""),
        ]
    )

    with patch("pexpect.spawn", side_effect=[about_child, stats_child]):
        result = provider_limits._probe_gemini(timeout_seconds=30)

    assert result.status == "unavailable"
    assert result.model == "Auto (Gemini 3)"
    assert "tier Gemini Code Assist in Google One AI Pro" in result.summary


def test_probe_claude_reads_usage_from_interactive_repl() -> None:
    usage_child = _build_interactive_child(
        [
            (0, ""),
            (
                0,
                (
                    "Current session\n"
                    "2% used\n"
                    "Resets 7pm (America/Sao_Paulo)\n\n"
                    "Current week (all models)\n"
                    "29% used\n"
                    "Resets Feb 26, 2pm (America/Sao_Paulo)\n"
                ),
            ),
            (0, ""),
        ]
    )

    with patch("pexpect.spawn", return_value=usage_child):
        result = provider_limits._probe_claude(timeout_seconds=30)

    assert result.status == "ok"
    assert len(result.entries) == 2
    assert result.entries[0].window == "current session"
    assert result.entries[0].percent_type == "used"
    assert result.entries[0].percent_value == 2
    assert result.entries[1].window == "current week"
    assert result.entries[1].percent_value == 29


def test_probe_claude_falls_back_to_cost_when_usage_has_no_entries() -> None:
    usage_child = _build_interactive_child(
        [
            (0, ""),
            (0, "No quota information found"),
            (0, ""),
        ]
    )
    cost_child = _build_interactive_child(
        [
            (0, ""),
            (
                0,
                (
                    "Current session\n"
                    "5% used\n"
                    "Resets 9pm (America/Sao_Paulo)\n"
                ),
            ),
            (0, ""),
        ]
    )

    with patch("pexpect.spawn", side_effect=[usage_child, cost_child]) as spawn_mock:
        result = provider_limits._probe_claude(timeout_seconds=30)

    assert spawn_mock.call_count == 2
    assert result.status == "ok"
    assert len(result.entries) == 1
    assert result.entries[0].window == "current session"
    assert result.entries[0].percent_value == 5
    assert result.source == "claude /cost"


def test_run_probe_command_uses_pexpect_and_returns_output() -> None:
    child = Mock()
    child.before = "prefix \x1b[32mok\x1b[0m"
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
    child.read.assert_not_called()
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
    message = "The command was not found or was not executable: codex."
    with patch(
        "pexpect.spawn",
        side_effect=pexpect.exceptions.ExceptionPexpect(message),
    ):
        result = provider_limits._run_probe_command(["codex", "exec", "/status"], timeout_seconds=1)

    assert result.return_code is None
    assert result.timed_out is False
    assert result.error == message
    assert result.output == ""


def test_run_probe_command_preserves_nonzero_exit_code() -> None:
    child = Mock()
    child.before = "invalid request"
    child.exitstatus = 2

    with patch("pexpect.spawn", return_value=child):
        result = provider_limits._run_probe_command(["gemini", "-p", "/status"], timeout_seconds=2)

    assert result.return_code == 2
    assert result.timed_out is False
    assert result.error is None
    assert result.output == "invalid request"
