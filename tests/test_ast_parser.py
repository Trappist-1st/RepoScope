from app.parsing.ast_parser import AstParser
from tests.conftest import SAMPLE_REPO


parser = AstParser()


def _read(rel: str) -> str:
    return (SAMPLE_REPO / rel).read_text(encoding="utf-8")


def test_parse_python_definitions():
    defs = parser.parse_definitions(_read("py_pkg/a.py"), "python")
    names = {(d.name, d.kind.value) for d in defs}
    assert ("greet", "function") in names
    assert ("Helper", "class") in names
    assert ("name", "method") in names
    assert ("shout", "method") in names
    helper = next(d for d in defs if d.name == "Helper")
    assert helper.start_line < helper.end_line
    shout = next(d for d in defs if d.name == "shout")
    assert shout.parent_name == "Helper"


def test_parse_javascript_definitions():
    defs = parser.parse_definitions(_read("js_pkg/util.js"), "javascript")
    names = {d.name for d in defs}
    assert "add" in names
    assert "Calculator" in names
    assert "multiply" in names


def test_parse_typescript_definitions():
    defs = parser.parse_definitions(_read("js_pkg/main.ts"), "typescript")
    names = {d.name for d in defs}
    assert "main" in names


def test_parse_java_definitions():
    defs = parser.parse_definitions(_read("java_pkg/Hello.java"), "java")
    names = {(d.name, d.kind.value) for d in defs}
    assert ("Hello", "class") in names
    assert ("greet", "method") in names
    assert ("shout", "method") in names
