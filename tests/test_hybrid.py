from app.retrieval.citations import format_citation, parse_citation
from app.retrieval.hybrid import reciprocal_rank_fusion, weighted_fusion


def test_citation_roundtrip():
    text = format_citation("py_pkg/a.py", 4, 5)
    assert text == "py_pkg/a.py:4-5"
    cite = parse_citation(text)
    assert cite.file_path == "py_pkg/a.py"
    assert cite.start_line == 4
    assert cite.end_line == 5


def test_rrf_prefers_items_ranked_high_in_both():
    fused = reciprocal_rank_fusion(
        {
            "vector": ["a", "b", "c"],
            "bm25": ["b", "a", "d"],
        },
        k=60,
    )
    ids = [cid for cid, _, _ in fused]
    assert ids[0] in {"a", "b"}
    assert set(ids) == {"a", "b", "c", "d"}


def test_weighted_fusion_minmax():
    fused = weighted_fusion(
        {
            "vector": {"a": 0.9, "b": 0.1},
            "bm25": {"a": 1.0, "b": 10.0},
        },
        {"vector": 0.5, "bm25": 0.5},
    )
    ids = [cid for cid, _, _ in fused]
    assert set(ids) == {"a", "b"}
