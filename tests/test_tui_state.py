import base64
import hashlib
import json
from pathlib import Path

import pytest

import council.tui_state as tui_state_module
from council.tui_state import (
    TUI_STATE_PASSPHRASE_ENV_VAR,
    TUI_STATE_PASSPHRASE_FILE_ENV_VAR,
    TUIStateCryptoError,
    _coerce_prompt_history,
    _decrypt_prompt_state,
    _encrypt_prompt_state,
    clear_tui_prompt_history,
    load_tui_state_payload,
    persist_tui_state_payload,
    read_raw_tui_state_payload,
    read_tui_state_passphrase,
)


class _FakeInvalidToken(Exception):
    pass


class _FakeHashes:
    class SHA256:
        pass


class _FakePBKDF2HMAC:
    def __init__(self, algorithm, length: int, salt: bytes, iterations: int):
        del algorithm
        self.length = length
        self.salt = salt
        self.iterations = iterations

    def derive(self, value: bytes) -> bytes:
        digest = hashlib.sha256(
            value + b"|" + self.salt + b"|" + str(self.iterations).encode("ascii")
        ).digest()
        return (digest * ((self.length // len(digest)) + 1))[: self.length]


class _FakeFernet:
    def __init__(self, key: bytes):
        self.key = key

    def encrypt(self, plaintext: bytes) -> bytes:
        return base64.urlsafe_b64encode(self.key + b":" + plaintext)

    def decrypt(self, token: bytes) -> bytes:
        decoded = base64.urlsafe_b64decode(token)
        prefix = self.key + b":"
        if not decoded.startswith(prefix):
            raise _FakeInvalidToken("invalid token")
        return decoded[len(prefix) :]


def _install_fake_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tui_state_module,
        "_load_crypto_primitives",
        lambda: (_FakeFernet, _FakeInvalidToken, _FakeHashes, _FakePBKDF2HMAC),
    )


def test_encrypt_and_load_roundtrip_with_passphrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_crypto(monkeypatch)
    state_path = tmp_path / "tui_state.json"
    payload = {
        "last_prompt": "segredo",
        "prompt_history": ["um", "dois"],
        "last_flow_config": "flow.example.json",
    }

    persist_tui_state_payload(state_path, payload, passphrase="senha-forte")

    raw_payload = read_raw_tui_state_payload(state_path)
    assert "encrypted_prompt_state" in raw_payload
    assert "last_prompt" not in raw_payload
    assert "prompt_history" not in raw_payload

    loaded_payload = load_tui_state_payload(state_path, passphrase="senha-forte")
    assert loaded_payload["last_prompt"] == "segredo"
    assert loaded_payload["prompt_history"] == ["um", "dois"]
    assert loaded_payload["last_flow_config"] == "flow.example.json"


def test_encrypt_uses_new_salt_and_changes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_crypto(monkeypatch)
    salts = iter([b"\x01" * 16, b"\x02" * 16])
    monkeypatch.setattr(tui_state_module.os, "urandom", lambda _: next(salts))
    prompt_state = {"last_prompt": "alpha", "prompt_history": ["beta"]}

    first_payload = _encrypt_prompt_state(prompt_state, "senha")
    second_payload = _encrypt_prompt_state(prompt_state, "senha")

    assert first_payload["salt"] != second_payload["salt"]
    assert first_payload["token"] != second_payload["token"]


def test_coerce_prompt_history_filters_malformed_entries() -> None:
    value = ["  ok  ", "", None, 123, "segredo", "  ", "segredo"]

    coerced = _coerce_prompt_history(value)

    assert coerced == ["ok", "segredo", "segredo"]


def test_decrypt_rejects_tampered_iterations_without_loading_crypto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tui_state_module,
        "_load_crypto_primitives",
        lambda: (_ for _ in ()).throw(AssertionError("não deveria carregar crypto")),
    )
    encrypted_state = {
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": "390000",
        "salt": base64.b64encode(b"\x01" * 16).decode("ascii"),
        "token": "dummy",
    }

    with pytest.raises(TUIStateCryptoError, match="Iterações PBKDF2 inválidas"):
        _decrypt_prompt_state(encrypted_state, passphrase="senha")


def test_clear_history_preserves_encrypted_schema_when_passphrase_is_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_crypto(monkeypatch)
    state_path = tmp_path / "tui_state.json"
    initial_payload = {
        "last_prompt": "segredo",
        "prompt_history": ["um", "dois"],
        "last_flow_config": "flow.example.json",
    }
    persist_tui_state_payload(state_path, initial_payload, passphrase="senha")

    cleared = clear_tui_prompt_history(state_path, passphrase="senha")

    assert cleared is True
    raw_payload = read_raw_tui_state_payload(state_path)
    assert "encrypted_prompt_state" in raw_payload
    assert "last_prompt" not in raw_payload
    assert "prompt_history" not in raw_payload

    loaded_payload = load_tui_state_payload(state_path, passphrase="senha")
    assert loaded_payload["last_prompt"] == ""
    assert loaded_payload["prompt_history"] == []
    assert loaded_payload["last_flow_config"] == "flow.example.json"


def test_read_tui_state_passphrase_supports_file_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passphrase_file = tmp_path / "passphrase.txt"
    passphrase_file.write_text("senha-via-arquivo\n", encoding="utf-8")
    monkeypatch.delenv(TUI_STATE_PASSPHRASE_ENV_VAR, raising=False)
    monkeypatch.setenv(TUI_STATE_PASSPHRASE_FILE_ENV_VAR, str(passphrase_file))

    assert read_tui_state_passphrase() == "senha-via-arquivo"


def test_read_tui_state_passphrase_env_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passphrase_file = tmp_path / "passphrase.txt"
    passphrase_file.write_text("senha-via-arquivo\n", encoding="utf-8")
    monkeypatch.setenv(TUI_STATE_PASSPHRASE_ENV_VAR, "senha-via-env")
    monkeypatch.setenv(TUI_STATE_PASSPHRASE_FILE_ENV_VAR, str(passphrase_file))

    assert read_tui_state_passphrase() == "senha-via-env"


def test_write_payload_temp_file_is_hardened_before_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "tui_state.json"
    captured: dict[str, int] = {}
    original_fchmod = tui_state_module.os.fchmod

    def recording_fchmod(fd: int, mode: int) -> None:
        captured["mode"] = mode
        original_fchmod(fd, mode)

    monkeypatch.setattr(tui_state_module.os, "fchmod", recording_fchmod)

    persist_tui_state_payload(state_path, {"last_prompt": "x", "prompt_history": []})

    assert captured["mode"] == 0o600
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["last_prompt"] == "x"
