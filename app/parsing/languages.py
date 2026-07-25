from __future__ import annotations

from functools import lru_cache

from tree_sitter import Language, Parser

# AST-parsed languages (tree-sitter).
AST_LANGUAGES: frozenset[str] = frozenset(
    {"python", "javascript", "typescript", "tsx", "java"}
)

# Indexed languages — includes text/markdown via fallback line chunking (no AST).
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".md": "markdown",
    ".markdown": "markdown",
}


def detect_language(file_path: str) -> str | None:
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    return SUPPORTED_EXTENSIONS.get(suffix)


@lru_cache(maxsize=8)
def _language_object(language: str) -> Language:
    if language == "python":
        import tree_sitter_python as tsp

        return Language(tsp.language())
    if language == "javascript":
        import tree_sitter_javascript as tsjs

        return Language(tsjs.language())
    if language == "typescript":
        import tree_sitter_typescript as tsts

        return Language(tsts.language_typescript())
    if language == "tsx":
        import tree_sitter_typescript as tsts

        return Language(tsts.language_tsx())
    if language == "java":
        import tree_sitter_java as tsjava

        return Language(tsjava.language())
    raise ValueError(f"Unsupported language: {language}")


def get_parser(language: str) -> Parser:
    parser = Parser(_language_object(language))
    return parser


def get_language(language: str) -> Language:
    return _language_object(language)
