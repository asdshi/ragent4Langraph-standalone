from src.ragent_backend.intent import detect_intent


def test_detect_intent_should_clarify_without_history() -> None:
    result = detect_intent("它多少钱", has_history=False)
    assert result.need_clarify is True
    assert result.confidence < 0.5


def test_detect_intent_should_not_clarify_with_history() -> None:
    result = detect_intent("它多少钱", has_history=True)
    assert result.need_clarify is False
    assert result.confidence > 0.8
