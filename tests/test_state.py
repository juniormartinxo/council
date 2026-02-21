import pytest

from council.state import CONTEXT_TRUNCATION_NOTICE, CouncilState


def test_get_full_context_returns_empty_when_history_is_empty() -> None:
    state = CouncilState()

    assert state.get_full_context() == ""


def test_get_full_context_truncates_oldest_content_when_limit_is_exceeded() -> None:
    state = CouncilState(max_context_chars=80)
    state.add_turn("Human", "user", "A" * 50)
    state.add_turn("Claude", "assistant", "B" * 50, action="Planejamento")

    context = state.get_full_context()

    assert context.startswith(CONTEXT_TRUNCATION_NOTICE)
    assert len(context) == 80
    assert context.endswith("B" * 50)


def test_state_rejects_non_positive_context_limit() -> None:
    with pytest.raises(ValueError, match="max_context_chars"):
        CouncilState(max_context_chars=0)


def test_state_rejects_invalid_context_limit_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COUNCIL_MAX_CONTEXT_CHARS", "-1")

    with pytest.raises(ValueError, match="COUNCIL_MAX_CONTEXT_CHARS"):
        CouncilState()


def test_get_full_context_supports_per_call_limit_override() -> None:
    state = CouncilState(max_context_chars=200)
    state.add_turn("Human", "user", "A" * 40)
    state.add_turn("Claude", "assistant", "B" * 40)

    context = state.get_full_context(max_chars=30)

    assert len(context) == 30
    assert context.startswith(CONTEXT_TRUNCATION_NOTICE[: min(30, len(CONTEXT_TRUNCATION_NOTICE))])


def test_get_full_context_rejects_non_positive_override() -> None:
    state = CouncilState(max_context_chars=200)

    with pytest.raises(ValueError, match="max_chars"):
        state.get_full_context(max_chars=0)
