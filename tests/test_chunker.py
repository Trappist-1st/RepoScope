from app.parsing.ast_parser import AstParser
from app.parsing.chunker import Chunker
from tests.conftest import SAMPLE_REPO


def test_ast_chunks_align_to_definitions():
    content = (SAMPLE_REPO / "py_pkg/a.py").read_text(encoding="utf-8")
    defs = AstParser().parse_definitions(content, "python")
    chunks = Chunker().chunk_file("py_pkg/a.py", content, defs, "python")

    kinds = {c.kind for c in chunks}
    assert "function" in kinds
    assert "class" in kinds
    # methods nested in class are not duplicated as separate chunks (v1)
    assert "method" not in kinds

    greet = next(c for c in chunks if c.symbol_name == "greet")
    assert "def greet" in greet.content
    assert greet.start_line >= 1


def test_fallback_chunks_by_line_count():
    content = (SAMPLE_REPO / "config.toml").read_text(encoding="utf-8")
    # pad content so fallback produces multiple chunks
    padded = (content + "\n") * 50
    chunks = Chunker(fallback_lines=10).chunk_file("config.toml", padded, [], None)
    assert len(chunks) >= 2
    assert all(c.kind == "fallback" for c in chunks)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 10
