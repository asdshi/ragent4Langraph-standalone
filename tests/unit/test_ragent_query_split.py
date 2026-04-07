from src.ragent_backend.intent import split_parallel_subqueries


def test_split_parallel_subjects_chinese() -> None:
    query = "华为和小米的营收是多少？"
    subs = split_parallel_subqueries(query)
    assert len(subs) >= 2
    assert any("华为" in item for item in subs)
    assert any("小米" in item for item in subs)


def test_split_parallel_subjects_and_keyword() -> None:
    query = "Compare revenue of Tesla and BYD"
    subs = split_parallel_subqueries(query)
    assert len(subs) >= 2
    assert any("Tesla" in item for item in subs)
    assert any("BYD" in item for item in subs)
