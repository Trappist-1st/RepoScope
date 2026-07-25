"""Load / normalize Phase-6 QA evaluation datasets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

QuestionType = Literal["summary", "dependency", "refactor"]
VALID_TYPES = {"summary", "dependency", "refactor"}


@dataclass
class GoldSpan:
    file_path: str
    start_line: int
    end_line: int

    def format(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass
class QAItem:
    id: str
    question: str
    question_type: QuestionType
    gold_spans: list[GoldSpan]
    repo_url: str | None = None
    repo_path: str | None = None
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def gold_citations(self) -> list[str]:
        return [g.format() for g in self.gold_spans]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").strip()


def _parse_span(value: Any) -> GoldSpan:
    if isinstance(value, str):
        path, _, span = value.rpartition(":")
        start_s, _, end_s = span.partition("-")
        return GoldSpan(
            file_path=_normalize_path(path),
            start_line=int(start_s),
            end_line=int(end_s),
        )
    if isinstance(value, dict):
        return GoldSpan(
            file_path=_normalize_path(str(value["file_path"])),
            start_line=int(value["start_line"]),
            end_line=int(value["end_line"]),
        )
    raise TypeError(f"Unsupported gold span type: {type(value)!r}")


def _coerce_question_type(raw: dict[str, Any]) -> QuestionType:
    qtype = raw.get("question_type") or raw.get("type") or "dependency"
    qtype = str(qtype).strip().lower()
    # Accept Chinese aliases from the prompt
    aliases = {
        "摘要": "summary",
        "摘要类": "summary",
        "依赖": "dependency",
        "依赖查询": "dependency",
        "依赖查询类": "dependency",
        "重构": "refactor",
        "重构建议": "refactor",
        "重构建议类": "refactor",
    }
    qtype = aliases.get(qtype, qtype)
    if qtype not in VALID_TYPES:
        raise ValueError(
            f"Invalid question_type={qtype!r} for id={raw.get('id')}; "
            f"expected one of {sorted(VALID_TYPES)}"
        )
    return qtype  # type: ignore[return-value]


def _coerce_gold(raw: dict[str, Any]) -> list[GoldSpan]:
    spans = raw.get("gold_spans")
    if spans is None:
        # Backward compat with Phase-2 skeleton
        spans = raw.get("relevant_citations") or []
    if not isinstance(spans, list):
        raise TypeError(f"gold_spans must be a list (id={raw.get('id')})")
    return [_parse_span(s) for s in spans]


def normalize_item(raw: dict[str, Any]) -> QAItem:
    item_id = str(raw.get("id") or "").strip()
    if not item_id:
        raise ValueError("Each QA row needs a non-empty 'id'")
    question = str(raw.get("question") or "").strip()
    if not question:
        raise ValueError(f"QA row {item_id} needs a non-empty 'question'")

    repo_url = raw.get("repo_url") or raw.get("repo") or None
    repo_path = raw.get("repo_path") or None
    if repo_url is not None:
        repo_url = str(repo_url).strip() or None
    if repo_path is not None:
        repo_path = str(repo_path).strip() or None
    if not repo_url and not repo_path:
        raise ValueError(f"QA row {item_id} needs repo_url and/or repo_path")

    return QAItem(
        id=item_id,
        question=question,
        question_type=_coerce_question_type(raw),
        gold_spans=_coerce_gold(raw),
        repo_url=repo_url,
        repo_path=repo_path,
        notes=str(raw.get("notes") or ""),
        raw=raw,
    )


def load_qa_dataset(path: Path) -> list[QAItem]:
    if not path.exists():
        return []
    items: list[QAItem] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON — {exc}") from exc
        items.append(normalize_item(raw))
    return items


def dump_qa_template_row() -> dict[str, Any]:
    """Canonical example row for docs / copy-paste."""
    return {
        "id": "q-001",
        "repo_url": "https://github.com/psf/requests.git",
        "repo_path": "data/eval_repos/requests",
        "question": "Where is Session.send implemented?",
        "question_type": "dependency",
        "gold_spans": [
            {
                "file_path": "requests/sessions.py",
                "start_line": 645,
                "end_line": 720,
            }
        ],
        "notes": "Annotate the span that *should* be retrieved, not the whole file.",
    }
