from __future__ import annotations

import shlex
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from council.config import FlowStep


@dataclass(frozen=True)
class BinaryPrerequisiteStatus:
    binary: str
    resolved_path: str | None
    is_available: bool
    is_world_writable_location: bool = False


def evaluate_flow_prerequisites(flow_steps: Sequence[FlowStep]) -> list[BinaryPrerequisiteStatus]:
    statuses: list[BinaryPrerequisiteStatus] = []

    for binary in collect_required_binaries(flow_steps):
        resolved = shutil.which(binary)
        if resolved is None:
            statuses.append(
                BinaryPrerequisiteStatus(
                    binary=binary,
                    resolved_path=None,
                    is_available=False,
                )
            )
            continue

        resolved_path = _normalize_path(resolved)
        directory = Path(resolved_path).parent
        statuses.append(
            BinaryPrerequisiteStatus(
                binary=binary,
                resolved_path=resolved_path,
                is_available=True,
                is_world_writable_location=_is_world_writable_directory(directory),
            )
        )

    return statuses


def collect_required_binaries(flow_steps: Sequence[FlowStep]) -> list[str]:
    required: list[str] = []
    seen: set[str] = set()

    for step in flow_steps:
        if not step.enabled:
            continue
        binary = _extract_binary_name(step.command)
        if binary is None or binary in seen:
            continue
        seen.add(binary)
        required.append(binary)

    return required


def find_missing_binaries(
    statuses: Sequence[BinaryPrerequisiteStatus],
) -> list[BinaryPrerequisiteStatus]:
    return [status for status in statuses if not status.is_available]


def find_world_writable_binary_locations(
    statuses: Sequence[BinaryPrerequisiteStatus],
) -> list[BinaryPrerequisiteStatus]:
    return [
        status
        for status in statuses
        if status.is_available and status.is_world_writable_location
    ]


def _extract_binary_name(command: str) -> str | None:
    try:
        command_tokens = shlex.split(command)
    except ValueError:
        return None

    if not command_tokens:
        return None

    binary_name = Path(command_tokens[0]).name.strip()
    return binary_name or None


def _normalize_path(path: str) -> str:
    candidate = Path(path)
    try:
        return str(candidate.resolve())
    except OSError:
        return str(candidate)


def _is_world_writable_directory(directory: Path) -> bool:
    try:
        mode = directory.stat().st_mode
    except OSError:
        return False
    return bool(mode & stat.S_IWOTH)
