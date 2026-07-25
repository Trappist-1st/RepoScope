from __future__ import annotations

import hashlib

from app.models.schemas import Chunk, Definition


def _make_chunk_id(file_path: str, start_line: int, end_line: int, name: str | None) -> str:
    raw = f"{file_path}|{start_line}|{end_line}|{name or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _slice_lines(lines: list[str], start_line: int, end_line: int) -> str:
    return "".join(lines[start_line - 1 : end_line])


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class Chunker:
    def __init__(self, fallback_lines: int = 80) -> None:
        self.fallback_lines = max(1, fallback_lines)

    def chunk_file(
        self,
        file_path: str,
        content: str,
        definitions: list[Definition],
        language: str | None,
    ) -> list[Chunk]:
        lines = content.splitlines(keepends=True)
        if not lines and content:
            lines = [content]

        if definitions:
            return self._chunk_from_definitions(file_path, lines, definitions, language)
        return self._fallback_chunks(file_path, lines, language)

    def _chunk_from_definitions(
        self,
        file_path: str,
        lines: list[str],
        definitions: list[Definition],
        language: str | None,
    ) -> list[Chunk]:
        # Prefer top-level / non-overlapping spans.
        # Methods nested inside a class that we already chunk as a whole class
        # are skipped so we don't duplicate (v1: whole class = one chunk).
        class_spans = [
            (d.start_line, d.end_line)
            for d in definitions
            if d.kind.value == "class"
        ]

        chunks: list[Chunk] = []
        for d in definitions:
            if d.kind.value == "method" and self._inside_any_span(d.start_line, d.end_line, class_spans):
                continue
            # Nested functions inside another function: keep them only if not
            # fully covered by a larger function/class we already emit.
            text = _slice_lines(lines, d.start_line, d.end_line)
            chunks.append(
                Chunk(
                    chunk_id=_make_chunk_id(file_path, d.start_line, d.end_line, d.name),
                    file_path=file_path,
                    start_line=d.start_line,
                    end_line=d.end_line,
                    content=text,
                    kind=d.kind.value,
                    symbol_name=d.name if not d.parent_name else f"{d.parent_name}.{d.name}",
                    language=language or d.language,
                    content_hash=_content_hash(text),
                )
            )

        # Also emit standalone top-level functions not covered above — already done.
        # Deduplicate by (start, end).
        unique: dict[tuple[int, int], Chunk] = {}
        for c in chunks:
            unique[(c.start_line, c.end_line)] = c
        return sorted(unique.values(), key=lambda c: c.start_line)

    @staticmethod
    def _inside_any_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
        for s, e in spans:
            if start > s and end <= e:
                return True
        return False

    def _fallback_chunks(
        self,
        file_path: str,
        lines: list[str],
        language: str | None,
    ) -> list[Chunk]:
        if not lines:
            return []
        chunks: list[Chunk] = []
        n = len(lines)
        for i in range(0, n, self.fallback_lines):
            start = i + 1
            end = min(i + self.fallback_lines, n)
            text = _slice_lines(lines, start, end)
            chunks.append(
                Chunk(
                    chunk_id=_make_chunk_id(file_path, start, end, None),
                    file_path=file_path,
                    start_line=start,
                    end_line=end,
                    content=text,
                    kind="fallback",
                    symbol_name=None,
                    language=language,
                    content_hash=_content_hash(text),
                )
            )
        return chunks
