from pathlib import Path

import pytest

import council.prerequisites as prerequisites_module
from council.config import FlowStep
from council.prerequisites import (
    collect_required_binaries,
    evaluate_flow_prerequisites,
    find_missing_binaries,
    find_world_writable_binary_locations,
)


def _build_step(command: str) -> FlowStep:
    return FlowStep(
        key="step",
        agent_name="Agent",
        role_desc="Role",
        command=command,
        instruction="instruction",
    )


def test_collect_required_binaries_deduplicates_preserving_order() -> None:
    steps = [
        _build_step("claude -p"),
        _build_step("gemini -p {input}"),
        _build_step("claude -p"),
        _build_step("codex exec --skip-git-repo-check"),
    ]

    binaries = collect_required_binaries(steps)

    assert binaries == ["claude", "gemini", "codex"]


def test_collect_required_binaries_ignores_disabled_steps() -> None:
    steps = [
        _build_step("claude -p"),
        FlowStep(
            key="disabled",
            agent_name="Agent",
            role_desc="Role",
            command="codex exec --skip-git-repo-check",
            instruction="instruction",
            enabled=False,
        ),
    ]

    binaries = collect_required_binaries(steps)

    assert binaries == ["claude"]


def test_evaluate_flow_prerequisites_marks_missing_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    steps = [
        _build_step("claude -p"),
        _build_step("codex exec --skip-git-repo-check"),
    ]

    def fake_which(binary: str) -> str | None:
        if binary == "codex":
            return None
        return f"/usr/bin/{binary}"

    monkeypatch.setattr(prerequisites_module.shutil, "which", fake_which)

    statuses = evaluate_flow_prerequisites(steps)
    missing = find_missing_binaries(statuses)

    assert len(missing) == 1
    assert missing[0].binary == "codex"
    assert missing[0].resolved_path is None


def test_evaluate_flow_prerequisites_flags_world_writable_binary_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bin_dir.chmod(0o777)

    binary_path = bin_dir / "claude"
    binary_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary_path.chmod(0o755)

    monkeypatch.setattr(prerequisites_module.shutil, "which", lambda _: str(binary_path))

    statuses = evaluate_flow_prerequisites([_build_step("claude -p")])
    risky = find_world_writable_binary_locations(statuses)

    assert len(risky) == 1
    assert risky[0].binary == "claude"
    assert risky[0].is_world_writable_location is True


def test_evaluate_flow_prerequisites_accepts_deepseek_api_provider_without_which_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called_binaries: list[str] = []

    def fake_which(binary: str) -> str | None:
        called_binaries.append(binary)
        return None

    monkeypatch.setattr(prerequisites_module.shutil, "which", fake_which)

    statuses = evaluate_flow_prerequisites([_build_step("deepseek --model deepseek-chat")])

    assert len(statuses) == 1
    assert statuses[0].binary == "deepseek"
    assert statuses[0].resolved_path == "https://api.deepseek.com"
    assert statuses[0].is_available is True
    assert called_binaries == []
