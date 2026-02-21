import pytest

from council.limits import read_positive_int_env


def test_read_positive_int_env_returns_default_for_missing_value(monkeypatch) -> None:
    monkeypatch.delenv("COUNCIL_LIMIT_TEST", raising=False)

    assert read_positive_int_env("COUNCIL_LIMIT_TEST", 123) == 123


def test_read_positive_int_env_raises_for_invalid_values(monkeypatch) -> None:
    for invalid in ["abc", "0", "-1"]:
        monkeypatch.setenv("COUNCIL_LIMIT_TEST", invalid)
        with pytest.raises(ValueError, match="COUNCIL_LIMIT_TEST"):
            read_positive_int_env("COUNCIL_LIMIT_TEST", 123)


def test_read_positive_int_env_returns_env_value_when_positive(monkeypatch) -> None:
    monkeypatch.setenv("COUNCIL_LIMIT_TEST", "456")

    assert read_positive_int_env("COUNCIL_LIMIT_TEST", 123) == 456
