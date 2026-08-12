"""Score multipliers to demote docs and boost source code in retrieval."""

from __future__ import annotations

_DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".adoc", ".html")
_SOURCE_SUFFIXES = (
    ".py",
    ".java",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".kt",
    ".kts",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".scala",
)
_CODE_KINDS = frozenset({"function", "method", "class", "module", "code"})
_DOC_PATH_MARKERS = ("/docs/", "/doc/", "/documentation/", "/changelog")


def is_doc_path(file_path: str | None) -> bool:
    p = (file_path or "").replace("\\", "/").lower()
    if not p:
        return False
    if any(m in p for m in _DOC_PATH_MARKERS) or p.startswith("docs/"):
        return True
    return p.endswith(_DOC_SUFFIXES)


def is_source_path(file_path: str | None, language: str | None = None) -> bool:
    p = (file_path or "").replace("\\", "/").lower()
    if language and language.lower() in {
        "python",
        "java",
        "javascript",
        "typescript",
        "tsx",
        "go",
        "rust",
        "kotlin",
        "csharp",
    }:
        return not is_doc_path(p)
    return p.endswith(_SOURCE_SUFFIXES)


def source_quality_multiplier(
    *,
    file_path: str | None,
    language: str | None = None,
    kind: str | None = None,
) -> float:
    """Return a multiplicative score factor (typically 0.35–1.25)."""
    path = (file_path or "").replace("\\", "/")
    if is_doc_path(path):
        # README at repo root is slightly less demoted than /docs/
        name = path.rsplit("/", 1)[-1].lower()
        if name in {"readme.md", "readme.markdown", "readme"}:
            return 0.55
        return 0.35

    mult = 1.0
    if is_source_path(path, language):
        mult *= 1.15
    kind_l = (kind or "").lower()
    if kind_l in _CODE_KINDS:
        mult *= 1.10
    elif kind_l in {"fallback", "doc", "markdown"}:
        mult *= 0.70
    # Prefer non-test application code slightly
    pl = path.lower()
    if "/test/" in pl or "/tests/" in pl or pl.endswith("_test.py") or pl.startswith("test_"):
        mult *= 0.90
    return mult
