from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


LAST_PROMPT_KEY = "last_prompt"
LAST_FLOW_CONFIG_KEY = "last_flow_config"
PROMPT_HISTORY_KEY = "prompt_history"
ENCRYPTED_PROMPT_STATE_KEY = "encrypted_prompt_state"
STATE_SCHEMA_VERSION_KEY = "state_schema_version"
STATE_SCHEMA_VERSION = 2
TUI_STATE_PASSPHRASE_ENV_VAR = "COUNCIL_TUI_STATE_PASSPHRASE"
TUI_STATE_PASSPHRASE_FILE_ENV_VAR = "COUNCIL_TUI_STATE_PASSPHRASE_FILE"

_PROMPT_STATE_VERSION = 1
_PROMPT_STATE_KDF = "pbkdf2-sha256"
_PROMPT_STATE_PBKDF2_ITERATIONS = 390_000
_PROMPT_STATE_SALT_BYTES = 16


class TUIStateCryptoError(Exception):
    """Falha na proteção criptográfica do estado da TUI."""


class TUIStateCryptoUnavailableError(TUIStateCryptoError):
    """Criptografia solicitada, mas dependências não estão disponíveis."""


def read_tui_state_passphrase() -> str:
    env_passphrase = os.getenv(TUI_STATE_PASSPHRASE_ENV_VAR, "").strip()
    if env_passphrase:
        return env_passphrase

    passphrase_file = os.getenv(TUI_STATE_PASSPHRASE_FILE_ENV_VAR, "").strip()
    if not passphrase_file:
        return ""

    try:
        return Path(passphrase_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise TUIStateCryptoError(
            f"Falha ao ler passphrase em {TUI_STATE_PASSPHRASE_FILE_ENV_VAR}: {exc}"
        ) from exc


def read_raw_tui_state_payload(path: Path) -> dict[str, object]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_tui_state_payload(path: Path, passphrase: str | None = None) -> dict[str, object]:
    payload = read_raw_tui_state_payload(path)
    encrypted_state = payload.get(ENCRYPTED_PROMPT_STATE_KEY)
    if not isinstance(encrypted_state, dict):
        return payload

    effective_passphrase = (passphrase or "").strip()
    if not effective_passphrase:
        raise TUIStateCryptoError(
            (
                f"O estado da TUI em '{path}' está criptografado. "
                f"Defina {TUI_STATE_PASSPHRASE_ENV_VAR} para recuperar o histórico."
            )
        )

    decrypted_prompt_state = _decrypt_prompt_state(encrypted_state, effective_passphrase)
    loaded_payload = dict(payload)
    loaded_payload.pop(ENCRYPTED_PROMPT_STATE_KEY, None)
    loaded_payload.update(decrypted_prompt_state)
    return loaded_payload


def persist_tui_state_payload(
    path: Path,
    payload: Mapping[str, object],
    passphrase: str | None = None,
) -> None:
    effective_passphrase = (passphrase or "").strip()
    payload_to_persist: dict[str, object] = dict(payload)

    if effective_passphrase:
        prompt_state = _extract_prompt_state(payload_to_persist)
        payload_to_persist.pop(LAST_PROMPT_KEY, None)
        payload_to_persist.pop(PROMPT_HISTORY_KEY, None)
        payload_to_persist[ENCRYPTED_PROMPT_STATE_KEY] = _encrypt_prompt_state(
            prompt_state=prompt_state,
            passphrase=effective_passphrase,
        )
    else:
        payload_to_persist.pop(ENCRYPTED_PROMPT_STATE_KEY, None)

    payload_to_persist[STATE_SCHEMA_VERSION_KEY] = STATE_SCHEMA_VERSION
    _write_tui_state_payload(path, payload_to_persist)


def clear_tui_prompt_history(path: Path, passphrase: str | None = None) -> bool:
    if not path.exists():
        return False

    current_payload = read_raw_tui_state_payload(path)
    cleared_payload = dict(current_payload)
    cleared_payload.pop(ENCRYPTED_PROMPT_STATE_KEY, None)
    cleared_payload[LAST_PROMPT_KEY] = ""
    cleared_payload[PROMPT_HISTORY_KEY] = []

    effective_passphrase = (passphrase or "").strip() or None
    persist_tui_state_payload(path, cleared_payload, passphrase=effective_passphrase)
    return True


def _write_tui_state_payload(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized_payload = json.dumps(payload, ensure_ascii=False, indent=2)

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
            delete=False,
        ) as temp_file:
            if hasattr(os, "fchmod"):
                os.fchmod(temp_file.fileno(), 0o600)
            temp_file.write(serialized_payload)
            temp_path = temp_file.name

        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except OSError:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise


def _extract_prompt_state(payload: Mapping[str, object]) -> dict[str, object]:
    return {
        LAST_PROMPT_KEY: _coerce_string(payload.get(LAST_PROMPT_KEY)),
        PROMPT_HISTORY_KEY: _coerce_prompt_history(payload.get(PROMPT_HISTORY_KEY)),
    }


def _coerce_string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_prompt_history(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    history: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned:
            history.append(cleaned)
    return history


def _encrypt_prompt_state(prompt_state: Mapping[str, object], passphrase: str) -> dict[str, object]:
    Fernet, _, hashes, PBKDF2HMAC = _load_crypto_primitives()
    salt = os.urandom(_PROMPT_STATE_SALT_BYTES)
    key = _derive_fernet_key(
        passphrase=passphrase,
        salt=salt,
        iterations=_PROMPT_STATE_PBKDF2_ITERATIONS,
        hashes=hashes,
        PBKDF2HMAC=PBKDF2HMAC,
    )
    cipher = Fernet(key)
    plaintext = json.dumps(prompt_state, ensure_ascii=False).encode("utf-8")
    token = cipher.encrypt(plaintext)
    return {
        "version": _PROMPT_STATE_VERSION,
        "kdf": _PROMPT_STATE_KDF,
        "iterations": _PROMPT_STATE_PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "token": token.decode("ascii"),
    }


def _decrypt_prompt_state(encrypted_state: Mapping[str, object], passphrase: str) -> dict[str, object]:
    version = encrypted_state.get("version")
    if version != _PROMPT_STATE_VERSION:
        raise TUIStateCryptoError("Versão de criptografia do estado da TUI não suportada.")

    if encrypted_state.get("kdf") != _PROMPT_STATE_KDF:
        raise TUIStateCryptoError("Algoritmo KDF do estado da TUI não suportado.")

    iterations = encrypted_state.get("iterations")
    if not isinstance(iterations, int) or iterations <= 0:
        raise TUIStateCryptoError("Iterações PBKDF2 inválidas no estado criptografado.")

    salt_value = encrypted_state.get("salt")
    token_value = encrypted_state.get("token")
    if not isinstance(salt_value, str) or not isinstance(token_value, str):
        raise TUIStateCryptoError("Payload criptografado inválido no estado da TUI.")

    try:
        salt = base64.b64decode(salt_value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise TUIStateCryptoError("Salt inválido no estado criptografado da TUI.") from exc

    Fernet, InvalidToken, hashes, PBKDF2HMAC = _load_crypto_primitives()
    key = _derive_fernet_key(
        passphrase=passphrase,
        salt=salt,
        iterations=iterations,
        hashes=hashes,
        PBKDF2HMAC=PBKDF2HMAC,
    )
    cipher = Fernet(key)

    try:
        plaintext = cipher.decrypt(token_value.encode("ascii"))
    except InvalidToken as exc:
        raise TUIStateCryptoError("Senha inválida ou estado criptografado corrompido.") from exc

    try:
        prompt_state_payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TUIStateCryptoError("Payload descriptografado inválido no estado da TUI.") from exc

    if not isinstance(prompt_state_payload, dict):
        raise TUIStateCryptoError("Payload descriptografado inválido no estado da TUI.")

    return _extract_prompt_state(prompt_state_payload)


def _derive_fernet_key(
    passphrase: str,
    salt: bytes,
    iterations: int,
    *,
    hashes: Any,
    PBKDF2HMAC: Any,
) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    key_material = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(key_material)


def _load_crypto_primitives() -> tuple[Any, Any, Any, Any]:
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ModuleNotFoundError as exc:
        raise TUIStateCryptoUnavailableError(
            "Criptografia de estado requer o pacote 'cryptography'."
        ) from exc

    return Fernet, InvalidToken, hashes, PBKDF2HMAC
