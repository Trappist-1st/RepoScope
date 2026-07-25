from __future__ import annotations

from app.retrieval.schemas import Citation


def format_citation(file_path: str, start_line: int, end_line: int) -> str:
    return Citation(file_path=file_path, start_line=start_line, end_line=end_line).format()


def parse_citation(text: str) -> Citation:
    """Parse 'path:start-end' into Citation."""
    path, _, span = text.rpartition(":")
    start_s, _, end_s = span.partition("-")
    return Citation(file_path=path, start_line=int(start_s), end_line=int(end_s))
