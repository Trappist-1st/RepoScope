from eval.dataset import load_qa_dataset, normalize_item
from eval.metrics import precision_at_k, recall_at_k, spans_overlap, parse_span
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_overlap_same_file_partial():
    a = parse_span("a.py:1-10")
    b = parse_span({"file_path": "a.py", "start_line": 8, "end_line": 20})
    assert spans_overlap(a, b)
    assert not spans_overlap(a, parse_span("b.py:1-10"))
    assert not spans_overlap(a, parse_span("a.py:11-12"))


def test_recall_precision_overlap_not_exact_string():
    gold = ["pkg/mod.py:10-20"]
    # Predicted chunk covers gold but is not identical
    predicted = ["pkg/mod.py:1-40", "other.py:1-2"]
    assert recall_at_k(predicted, gold, k=5) == 1.0
    assert precision_at_k(predicted, gold, k=5) == 0.5


def test_normalize_item_accepts_chinese_type_and_shorthand_spans():
    item = normalize_item(
        {
            "id": "t1",
            "repo_path": "tests/fixtures/sample_repo",
            "question": "where is greet?",
            "question_type": "依赖查询类",
            "gold_spans": ["py_pkg/a.py:4-5"],
        }
    )
    assert item.question_type == "dependency"
    assert item.gold_citations == ["py_pkg/a.py:4-5"]


def test_load_qa_dataset_smoke():
    path = ROOT / "eval" / "datasets" / "qa_dataset.jsonl"
    items = load_qa_dataset(path)
    assert len(items) >= 3
    assert all(i.gold_spans for i in items)
    assert {i.question_type for i in items} <= {"summary", "dependency", "refactor"}
