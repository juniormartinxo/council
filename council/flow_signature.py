from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from council.paths import get_council_home


FLOW_SIGNATURE_VERSION = 1
FLOW_SIGNATURE_ALGORITHM = "ed25519"
FLOW_SIGNATURE_REQUIRED_ENV_VAR = "COUNCIL_REQUIRE_FLOW_SIGNATURE"
FLOW_TRUSTED_KEYS_DIR_ENV_VAR = "COUNCIL_TRUSTED_FLOW_KEYS_DIR"

_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSY_ENV_VALUES = {"", "0", "false", "no", "off"}
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class FlowSignatureError(Exception):
    """Falha de assinatura/verificação de fluxo."""


class FlowSignatureCryptoUnavailableError(FlowSignatureError):
    """Dependências criptográficas indisponíveis."""


class FlowSignatureVerificationError(FlowSignatureError):
    """Assinatura ausente, inválida ou não confiável."""


@dataclass(frozen=True)
class FlowSignatureMetadata:
    version: int
    algorithm: str
    key_id: str
    signature_b64: str


def parse_signature_required_from_env() -> bool:
    raw_value = os.getenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, "").strip().lower()
    if raw_value in _TRUTHY_ENV_VALUES:
        return True
    if raw_value in _FALSY_ENV_VALUES:
        return False

    allowed_values = ", ".join(sorted(_TRUTHY_ENV_VALUES | _FALSY_ENV_VALUES))
    raise FlowSignatureError(
        (
            f"Valor inválido em {FLOW_SIGNATURE_REQUIRED_ENV_VAR}: '{raw_value}'. "
            f"Use um dos valores: {allowed_values}."
        )
    )


def get_signature_file_path(flow_path: Path, signature_path: Path | None = None) -> Path:
    if signature_path is not None:
        return signature_path.expanduser()
    return flow_path.with_name(f"{flow_path.name}.sig")


def get_trusted_flow_keys_dir(create: bool = False) -> Path:
    override = os.getenv(FLOW_TRUSTED_KEYS_DIR_ENV_VAR, "").strip()
    if override:
        trusted_dir = Path(override).expanduser()
    else:
        trusted_dir = get_council_home(create=create) / "trusted_flow_keys"

    if create:
        trusted_dir.mkdir(parents=True, exist_ok=True)
        _harden_permissions(trusted_dir, mode=0o700)
    return trusted_dir


def trust_flow_public_key(
    public_key_path: Path,
    key_id: str,
    *,
    overwrite: bool = False,
) -> Path:
    source_path = public_key_path.expanduser()
    if not source_path.exists() or not source_path.is_file():
        raise FlowSignatureError(f"Arquivo de chave pública não encontrado: '{source_path}'.")

    normalized_key_id = normalize_key_id(key_id)
    destination = get_trusted_flow_keys_dir(create=True) / f"{normalized_key_id}.pem"
    if destination.exists() and not overwrite:
        raise FlowSignatureError(
            f"A chave '{normalized_key_id}' já existe em '{destination}'. Use --overwrite para substituir."
        )

    key_bytes = _read_file_bytes(source_path, label="chave pública")
    _load_public_key(key_bytes=key_bytes, key_path=source_path)
    _secure_write_bytes(destination, key_bytes, mode=0o600)
    return destination


def generate_flow_signing_keypair(
    private_key_path: Path,
    public_key_path: Path,
    *,
    overwrite: bool = False,
) -> None:
    private_target = private_key_path.expanduser()
    public_target = public_key_path.expanduser()

    if private_target.exists() and not overwrite:
        raise FlowSignatureError(
            f"O arquivo de chave privada já existe: '{private_target}'. Use --overwrite para substituir."
        )
    if public_target.exists() and not overwrite:
        raise FlowSignatureError(
            f"O arquivo de chave pública já existe: '{public_target}'. Use --overwrite para substituir."
        )

    _, serialization, Ed25519PrivateKey, _ = _load_crypto_primitives()
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    _secure_write_bytes(private_target, private_bytes, mode=0o600)
    _secure_write_bytes(public_target, public_bytes, mode=0o600)


def sign_flow_file(
    flow_path: Path,
    private_key_path: Path,
    key_id: str,
    *,
    signature_path: Path | None = None,
    overwrite: bool = False,
) -> Path:
    target_flow = flow_path.expanduser()
    _ensure_regular_file(target_flow, label="flow.json")
    target_private_key = private_key_path.expanduser()
    _ensure_regular_file(target_private_key, label="chave privada")

    output_signature_path = get_signature_file_path(target_flow, signature_path)
    if output_signature_path.exists() and not overwrite:
        raise FlowSignatureError(
            f"O arquivo de assinatura já existe: '{output_signature_path}'. Use --overwrite para substituir."
        )

    normalized_key_id = normalize_key_id(key_id)
    flow_content = _read_file_bytes(target_flow, label="flow.json")
    private_key_bytes = _read_file_bytes(target_private_key, label="chave privada")
    private_key = _load_private_key(key_bytes=private_key_bytes, key_path=target_private_key)
    signature = private_key.sign(flow_content)

    payload = {
        "version": FLOW_SIGNATURE_VERSION,
        "algorithm": FLOW_SIGNATURE_ALGORITHM,
        "key_id": normalized_key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    serialized_payload = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _secure_write_bytes(output_signature_path, serialized_payload, mode=0o600)
    return output_signature_path


def verify_flow_signature(
    flow_path: Path,
    *,
    signature_path: Path | None = None,
    require_signature: bool = False,
    public_key_path: Path | None = None,
    trusted_keys_dir: Path | None = None,
    flow_content: bytes | None = None,
) -> bool:
    target_flow = flow_path.expanduser()
    _ensure_regular_file(target_flow, label="flow.json")
    target_signature_path = get_signature_file_path(target_flow, signature_path)
    if not target_signature_path.exists():
        if require_signature:
            raise FlowSignatureVerificationError(
                (
                    f"Assinatura ausente para '{target_flow}'. "
                    f"Esperado arquivo '{target_signature_path}'."
                )
            )
        return False
    _ensure_regular_file(target_signature_path, label="assinatura")

    metadata = load_signature_metadata(target_signature_path)
    signature_bytes = _decode_signature_bytes(metadata.signature_b64)

    if flow_content is None:
        flow_content = _read_file_bytes(target_flow, label="flow.json")

    verification_public_key_path = _resolve_public_key_path(
        key_id=metadata.key_id,
        explicit_public_key_path=public_key_path,
        trusted_keys_dir=trusted_keys_dir,
    )
    public_key_bytes = _read_file_bytes(verification_public_key_path, label="chave pública")
    public_key = _load_public_key(
        key_bytes=public_key_bytes,
        key_path=verification_public_key_path,
    )

    InvalidSignature, _, _, _ = _load_crypto_primitives()
    try:
        public_key.verify(signature_bytes, flow_content)
    except InvalidSignature as exc:
        raise FlowSignatureVerificationError(
            (
                f"Assinatura inválida para '{target_flow}' "
                f"(key_id='{metadata.key_id}', assinatura='{target_signature_path}')."
            )
        ) from exc

    return True


def load_signature_metadata(signature_path: Path) -> FlowSignatureMetadata:
    payload_bytes = _read_file_bytes(signature_path, label="assinatura")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FlowSignatureError(
            f"Arquivo de assinatura inválido em '{signature_path}': esperado JSON UTF-8."
        ) from exc

    if not isinstance(payload, dict):
        raise FlowSignatureError(f"Assinatura inválida em '{signature_path}': esperado objeto JSON.")

    version = payload.get("version")
    if version != FLOW_SIGNATURE_VERSION:
        raise FlowSignatureError(
            (
                f"Versão de assinatura não suportada em '{signature_path}': '{version}'. "
                f"Esperado: {FLOW_SIGNATURE_VERSION}."
            )
        )

    algorithm = payload.get("algorithm")
    if algorithm != FLOW_SIGNATURE_ALGORITHM:
        raise FlowSignatureError(
            (
                f"Algoritmo de assinatura não suportado em '{signature_path}': '{algorithm}'. "
                f"Esperado: {FLOW_SIGNATURE_ALGORITHM}."
            )
        )

    key_id = payload.get("key_id")
    signature_b64 = payload.get("signature")
    if not isinstance(key_id, str) or not key_id.strip():
        raise FlowSignatureError(f"Assinatura inválida em '{signature_path}': campo 'key_id' ausente.")
    if not isinstance(signature_b64, str) or not signature_b64.strip():
        raise FlowSignatureError(
            f"Assinatura inválida em '{signature_path}': campo 'signature' ausente."
        )

    return FlowSignatureMetadata(
        version=version,
        algorithm=algorithm,
        key_id=normalize_key_id(key_id),
        signature_b64=signature_b64.strip(),
    )


def normalize_key_id(raw_key_id: str) -> str:
    key_id = raw_key_id.strip()
    if not _KEY_ID_PATTERN.fullmatch(key_id):
        raise FlowSignatureError(
            (
                f"key_id inválido: '{raw_key_id}'. "
                "Use apenas letras, números, '.', '_' ou '-' (1-64 chars)."
            )
        )
    return key_id


def _resolve_public_key_path(
    *,
    key_id: str,
    explicit_public_key_path: Path | None,
    trusted_keys_dir: Path | None,
) -> Path:
    if explicit_public_key_path is not None:
        key_path = explicit_public_key_path.expanduser()
        _ensure_regular_file(key_path, label="chave pública")
        return key_path

    trusted_dir = trusted_keys_dir.expanduser() if trusted_keys_dir is not None else get_trusted_flow_keys_dir()
    trusted_key_path = trusted_dir / f"{key_id}.pem"
    if not trusted_key_path.exists():
        raise FlowSignatureVerificationError(
            (
                f"Chave pública não confiada para key_id='{key_id}'. "
                f"Esperado arquivo '{trusted_key_path}'. Use 'council flow trust'."
            )
        )
    _ensure_regular_file(trusted_key_path, label="chave pública")
    return trusted_key_path


def _decode_signature_bytes(signature_b64: str) -> bytes:
    try:
        return base64.b64decode(signature_b64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise FlowSignatureError("Campo 'signature' inválido: esperado base64 válido.") from exc


def _ensure_regular_file(path: Path, *, label: str) -> None:
    if not path.exists():
        raise FlowSignatureError(f"Arquivo de {label} não encontrado: '{path}'.")
    if path.is_symlink():
        raise FlowSignatureError(
            f"O caminho de {label} não pode ser link simbólico: '{path}'."
        )
    if not path.is_file():
        raise FlowSignatureError(f"O caminho de {label} não é um arquivo: '{path}'.")


def _read_file_bytes(path: Path, *, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise FlowSignatureError(f"Falha ao ler {label} em '{path}': {exc}") from exc


def _secure_write_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _harden_permissions(path.parent, mode=0o700)

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
            delete=False,
        ) as temp_file:
            if hasattr(os, "fchmod"):
                os.fchmod(temp_file.fileno(), mode)
            temp_file.write(payload)
            temp_path = temp_file.name

        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        os.chmod(path, mode)
    except OSError as exc:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise FlowSignatureError(f"Falha ao gravar arquivo seguro em '{path}': {exc}") from exc


def _harden_permissions(path: Path, *, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        # Em alguns ambientes (ex.: Windows), chmod pode não ser plenamente suportado.
        return


def _load_private_key(*, key_bytes: bytes, key_path: Path) -> Any:
    _, serialization, Ed25519PrivateKey, _ = _load_crypto_primitives()
    try:
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except (TypeError, ValueError) as exc:
        raise FlowSignatureError(f"Chave privada inválida em '{key_path}'.") from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise FlowSignatureError(
            f"Tipo de chave privada não suportado em '{key_path}'. Use Ed25519 em PEM."
        )
    return key


def _load_public_key(*, key_bytes: bytes, key_path: Path) -> Any:
    _, serialization, _, Ed25519PublicKey = _load_crypto_primitives()
    try:
        key = serialization.load_pem_public_key(key_bytes)
    except ValueError as exc:
        raise FlowSignatureError(f"Chave pública inválida em '{key_path}'.") from exc

    if not isinstance(key, Ed25519PublicKey):
        raise FlowSignatureError(
            f"Tipo de chave pública não suportado em '{key_path}'. Use Ed25519 em PEM."
        )
    return key


def _load_crypto_primitives() -> tuple[Any, Any, Any, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ModuleNotFoundError as exc:
        raise FlowSignatureCryptoUnavailableError(
            "Assinatura de fluxo requer o pacote opcional 'cryptography'."
        ) from exc

    return InvalidSignature, serialization, Ed25519PrivateKey, Ed25519PublicKey
