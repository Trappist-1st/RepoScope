"""Retrieval metrics with line-range overlap matching.

Exact citation-string equality is too brittle for code RAG: AST chunk boundaries
rarely equal human gold spans. A prediction counts as a hit when the file path
matches and line ranges overlap.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.citations import parse_citation


@dataclass(frozen=True)
class Span:
    file_path: str
    start_line: int
    end_line: int

    def format(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").strip()


def parse_span(value: str | dict) -> Span:
    if isinstance(value, dict):
        return Span(
            file_path=normalize_path(str(value["file_path"])),
            start_line=int(value["start_line"]),
            end_line=int(value["end_line"]),
        )
    cite = parse_citation(value)
    return Span(
        file_path=normalize_path(cite.file_path),
        start_line=cite.start_line,
        end_line=cite.end_line,
    )


def spans_overlap(a: Span, b: Span) -> bool:
    if normalize_path(a.file_path) != normalize_path(b.file_path):
        return False
    return a.start_line <= b.end_line and b.start_line <= a.end_line


def recall_at_k(predicted: list[str], gold: list[str | dict], k: int = 5) -> float:
    """Fraction of gold spans hit by at least one of the top-k predictions."""
    if not gold:
        return 0.0
    preds = [parse_span(p) for p in predicted[:k]]
    golds = [parse_span(g) for g in gold]
    hit = sum(1 for g in golds if any(spans_overlap(p, g) for p in preds))
    return hit / len(golds)


def precision_at_k(predicted: list[str], gold: list[str | dict], k: int = 5) -> float:
    """Fraction of top-k predictions that hit at least one gold span."""
    top = predicted[:k]
    if not top:
        return 0.0
    if not gold:
        return 0.0
    preds = [parse_span(p) for p in top]
    golds = [parse_span(g) for g in gold]
    hit = sum(1 for p in preds if any(spans_overlap(p, g) for g in golds))
    return hit / len(preds)


def macro_average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
