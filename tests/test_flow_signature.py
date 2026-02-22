import json
from pathlib import Path

import pytest

import council.flow_signature as signature_module
from council.flow_signature import (
    FLOW_SIGNATURE_REQUIRED_ENV_VAR,
    FlowSignatureError,
    FlowSignatureVerificationError,
    generate_flow_signing_keypair,
    load_signature_metadata,
    normalize_key_id,
    parse_signature_required_from_env,
    sign_flow_file,
    trust_flow_public_key,
    verify_flow_signature,
)
from council.paths import COUNCIL_HOME_ENV_VAR


class _FakeInvalidSignature(Exception):
    pass


class _FakeEd25519PrivateKey:
    _PEM_PREFIX = b"FAKE-PRIVATE:"

    def __init__(self, secret: bytes):
        self.secret = secret

    @classmethod
    def generate(cls):
        return cls(b"generated-secret")

    def sign(self, payload: bytes) -> bytes:
        return b"sig:" + self.secret + b":" + payload

    def public_key(self) -> "_FakeEd25519PublicKey":
        return _FakeEd25519PublicKey(self.secret)

    def private_bytes(self, *, encoding, format, encryption_algorithm) -> bytes:
        del encoding, format, encryption_algorithm
        return self._PEM_PREFIX + self.secret


class _FakeEd25519PublicKey:
    _PEM_PREFIX = b"FAKE-PUBLIC:"

    def __init__(self, secret: bytes):
        self.secret = secret

    def verify(self, signature: bytes, payload: bytes) -> None:
        expected = b"sig:" + self.secret + b":" + payload
        if signature != expected:
            raise _FakeInvalidSignature("invalid signature")

    def public_bytes(self, *, encoding, format) -> bytes:
        del encoding, format
        return self._PEM_PREFIX + self.secret


class _FakeSerialization:
    class Encoding:
        PEM = object()

    class PrivateFormat:
        PKCS8 = object()

    class PublicFormat:
        SubjectPublicKeyInfo = object()

    @staticmethod
    def NoEncryption():
        return object()

    @staticmethod
    def load_pem_private_key(value: bytes, password=None):
        del password
        if not value.startswith(_FakeEd25519PrivateKey._PEM_PREFIX):
            raise ValueError("invalid fake private key")
        secret = value[len(_FakeEd25519PrivateKey._PEM_PREFIX) :]
        return _FakeEd25519PrivateKey(secret)

    @staticmethod
    def load_pem_public_key(value: bytes):
        if not value.startswith(_FakeEd25519PublicKey._PEM_PREFIX):
            raise ValueError("invalid fake public key")
        secret = value[len(_FakeEd25519PublicKey._PEM_PREFIX) :]
        return _FakeEd25519PublicKey(secret)


@pytest.fixture
def fake_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        signature_module,
        "_load_crypto_primitives",
        lambda: (
            _FakeInvalidSignature,
            _FakeSerialization,
            _FakeEd25519PrivateKey,
            _FakeEd25519PublicKey,
        ),
    )


def _write_minimal_flow(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "key": "step_1",
                    "agent_name": "Agent",
                    "role_desc": "Role",
                    "command": "codex exec",
                    "instruction": "Run",
                }
            ]
        ),
        encoding="utf-8",
    )


def test_parse_signature_required_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, "true")
    assert parse_signature_required_from_env() is True

    monkeypatch.setenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, "0")
    assert parse_signature_required_from_env() is False


@pytest.mark.parametrize("raw_value", ["1", "yes", "on", "TRUE", "YeS"])
def test_parse_signature_required_from_env_accepts_truthy_values(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    monkeypatch.setenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, raw_value)
    assert parse_signature_required_from_env() is True


@pytest.mark.parametrize("raw_value", ["", "0", "false", "no", "off", "OFF"])
def test_parse_signature_required_from_env_accepts_falsy_values(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    monkeypatch.setenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, raw_value)
    assert parse_signature_required_from_env() is False


def test_parse_signature_required_from_env_rejects_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLOW_SIGNATURE_REQUIRED_ENV_VAR, "talvez")

    with pytest.raises(FlowSignatureError, match=FLOW_SIGNATURE_REQUIRED_ENV_VAR):
        parse_signature_required_from_env()


def test_sign_and_verify_flow_signature_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_crypto: None,
) -> None:
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(tmp_path / ".council-home"))
    flow_path = tmp_path / "flow.json"
    _write_minimal_flow(flow_path)

    private_key_path = tmp_path / "author.key.pem"
    public_key_path = tmp_path / "author.pub.pem"
    generate_flow_signing_keypair(private_key_path, public_key_path)
    trusted_path = trust_flow_public_key(public_key_path, "author-v1")
    signature_path = sign_flow_file(flow_path, private_key_path, "author-v1")

    assert trusted_path.exists()
    assert signature_path.exists()
    assert verify_flow_signature(flow_path, require_signature=True) is True


def test_verify_requires_signature_when_enabled(
    tmp_path: Path,
    fake_crypto: None,
) -> None:
    flow_path = tmp_path / "flow.json"
    _write_minimal_flow(flow_path)

    with pytest.raises(FlowSignatureVerificationError, match="Assinatura ausente"):
        verify_flow_signature(flow_path, require_signature=True)

    assert verify_flow_signature(flow_path, require_signature=False) is False


def test_verify_rejects_tampered_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_crypto: None,
) -> None:
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(tmp_path / ".council-home"))
    flow_path = tmp_path / "flow.json"
    _write_minimal_flow(flow_path)

    private_key_path = tmp_path / "author.key.pem"
    public_key_path = tmp_path / "author.pub.pem"
    generate_flow_signing_keypair(private_key_path, public_key_path)
    trust_flow_public_key(public_key_path, "author-v1")
    sign_flow_file(flow_path, private_key_path, "author-v1")

    _write_minimal_flow(flow_path)
    flow_path.write_text(flow_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(FlowSignatureVerificationError, match="Assinatura inválida"):
        verify_flow_signature(flow_path, require_signature=True)


def test_trust_flow_public_key_rejects_existing_key_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_crypto: None,
) -> None:
    monkeypatch.setenv(COUNCIL_HOME_ENV_VAR, str(tmp_path / ".council-home"))
    private_key_path = tmp_path / "author.key.pem"
    public_key_path = tmp_path / "author.pub.pem"
    generate_flow_signing_keypair(private_key_path, public_key_path)

    trust_flow_public_key(public_key_path, "author-v1")
    with pytest.raises(FlowSignatureError, match="já existe"):
        trust_flow_public_key(public_key_path, "author-v1")


def test_generate_flow_signing_keypair_rejects_existing_files_without_overwrite(
    tmp_path: Path,
    fake_crypto: None,
) -> None:
    private_key_path = tmp_path / "author.key.pem"
    public_key_path = tmp_path / "author.pub.pem"
    private_key_path.write_text("existing", encoding="utf-8")
    public_key_path.write_text("existing", encoding="utf-8")

    with pytest.raises(FlowSignatureError, match="já existe"):
        generate_flow_signing_keypair(private_key_path, public_key_path, overwrite=False)


@pytest.mark.parametrize("value", ["", ".invalid", "inv alid", "inv/valid", "ç"])
def test_normalize_key_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(FlowSignatureError, match="key_id inválido"):
        normalize_key_id(value)


def test_load_signature_metadata_rejects_malformed_json(tmp_path: Path) -> None:
    signature_path = tmp_path / "flow.json.sig"
    signature_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(FlowSignatureError, match="esperado JSON UTF-8"):
        load_signature_metadata(signature_path)


def test_load_signature_metadata_rejects_missing_required_fields(tmp_path: Path) -> None:
    signature_path = tmp_path / "flow.json.sig"
    signature_path.write_text(
        json.dumps({"version": 1, "algorithm": "ed25519", "key_id": "author-v1"}),
        encoding="utf-8",
    )

    with pytest.raises(FlowSignatureError, match="campo 'signature' ausente"):
        load_signature_metadata(signature_path)


def test_secure_write_bytes_wraps_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_path = tmp_path / "target.txt"

    class _BrokenTempFile:
        def __enter__(self):
            raise OSError("permission denied")

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

    monkeypatch.setattr(
        signature_module.tempfile,
        "NamedTemporaryFile",
        lambda **_kwargs: _BrokenTempFile(),
    )

    with pytest.raises(FlowSignatureError, match="Falha ao gravar arquivo seguro"):
        signature_module._secure_write_bytes(target_path, b"x", mode=0o600)


def test_verify_rejects_signature_directory(tmp_path: Path) -> None:
    flow_path = tmp_path / "flow.json"
    _write_minimal_flow(flow_path)
    signature_path = tmp_path / "flow.json.sig"
    signature_path.mkdir()

    with pytest.raises(FlowSignatureError, match="não é um arquivo"):
        verify_flow_signature(flow_path, signature_path=signature_path, require_signature=True)


def test_verify_rejects_signature_symlink(tmp_path: Path) -> None:
    flow_path = tmp_path / "flow.json"
    _write_minimal_flow(flow_path)
    real_signature = tmp_path / "real.sig"
    real_signature.write_text("{}", encoding="utf-8")
    signature_symlink = tmp_path / "flow.json.sig"
    signature_symlink.symlink_to(real_signature)

    with pytest.raises(FlowSignatureError, match="link simbólico"):
        verify_flow_signature(flow_path, signature_path=signature_symlink, require_signature=True)
