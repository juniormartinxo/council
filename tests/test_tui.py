import os
import stat
from pathlib import Path

import pytest

from council.paths import COUNCIL_HOME_ENV_VAR
from council.tui import CouncilTextualApp


def _build_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[CouncilTextualApp, Path]:
    council_home = tmp_path / ".council-home"
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(council_home))
    monkeypatch.setattr(CouncilTextualApp, "STATE_FILE_PATH", council_home / "tui_state.json")
    return CouncilTextualApp(), council_home


def test_save_clipboard_fallback_uses_council_home_and_secure_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, council_home = _build_app(tmp_path, monkeypatch)

    saved_path, directory_secured = app._save_clipboard_fallback(
        payload="segredo",
        safe_label="stream_geral",
    )

    assert saved_path.parent == council_home / app.CLIPBOARD_FALLBACK_DIR_NAME
    assert saved_path.read_text(encoding="utf-8") == "segredo"
    assert directory_secured is True
    assert stat.S_IMODE(saved_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved_path.parent.stat().st_mode) == 0o700
    assert saved_path.name.startswith("council_stream_geral_")


def test_cleanup_clipboard_fallback_files_removes_only_expired_prefixed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, council_home = _build_app(tmp_path, monkeypatch)
    clipboard_dir = council_home / app.CLIPBOARD_FALLBACK_DIR_NAME
    clipboard_dir.mkdir(parents=True, exist_ok=True)

    expired_file = clipboard_dir / "council_expired.txt"
    fresh_file = clipboard_dir / "council_fresh.txt"
    unrelated_file = clipboard_dir / "notes.txt"
    for path in (expired_file, fresh_file, unrelated_file):
        path.write_text("payload", encoding="utf-8")

    os.utime(expired_file, (10, 10))
    os.utime(fresh_file, (95, 95))
    os.utime(unrelated_file, (10, 10))

    monkeypatch.setattr(app, "CLIPBOARD_FALLBACK_RETENTION_SECONDS", 10)

    app._cleanup_clipboard_fallback_files(clipboard_dir, now=100)

    assert not expired_file.exists()
    assert fresh_file.exists()
    assert unrelated_file.exists()


def test_copy_text_payload_persists_fallback_when_clipboard_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, council_home = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []

    def failing_copy(_: str) -> None:
        raise RuntimeError("clipboard unavailable")

    monkeypatch.setattr(app, "copy_to_clipboard", failing_copy)
    monkeypatch.setattr(
        app,
        "set_status",
        lambda message, style="": statuses.append((message, style)),
    )

    app._copy_text_payload(payload="conteudo sensivel", label="stream_geral", empty_message="vazio")

    fallback_files = list((council_home / app.CLIPBOARD_FALLBACK_DIR_NAME).glob("council_*.txt"))

    assert len(fallback_files) == 1
    assert fallback_files[0].read_text(encoding="utf-8") == "conteudo sensivel"
    assert statuses[-1][1] == "yellow"
    assert "Clipboard indisponível. Conteúdo salvo em" in statuses[-1][0]


def test_copy_text_payload_warns_when_directory_permissions_cannot_be_restricted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    statuses: list[tuple[str, str]] = []

    def failing_copy(_: str) -> None:
        raise RuntimeError("no clipboard")

    monkeypatch.setattr(app, "copy_to_clipboard", failing_copy)
    monkeypatch.setattr(
        app,
        "set_status",
        lambda message, style="": statuses.append((message, style)),
    )
    monkeypatch.setattr(app, "_secure_directory_permissions", lambda _: False)
    app._copy_text_payload(payload="conteudo sensivel", label="stream_geral", empty_message="vazio")

    assert statuses[-1][1] == "yellow"
    assert "aviso: permissões do diretório não puderam ser restritas" in statuses[-1][0]
