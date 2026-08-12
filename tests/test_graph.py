from pathlib import Path

from app.graph.builder import DependencyGraphBuilder
from app.graph.query import callees_of, callers_of, file_imports
from app.parsing.ast_parser import AstParser
from tests.conftest import SAMPLE_REPO

FIXTURES = Path(__file__).parent / "fixtures"
SPRING = FIXTURES / "flow_spring_login"


def test_python_import_and_call_edges():
    parser = AstParser()
    files = {
        "py_pkg/a.py": (SAMPLE_REPO / "py_pkg/a.py").read_text(encoding="utf-8"),
        "py_pkg/b.py": (SAMPLE_REPO / "py_pkg/b.py").read_text(encoding="utf-8"),
    }
    definitions_by_file = {
        path: parser.parse_definitions(content, "python") for path, content in files.items()
    }
    graph = DependencyGraphBuilder().build(
        repo_id="test",
        commit_hash=None,
        files=files,
        definitions_by_file=definitions_by_file,
    )

    imports = file_imports(graph, "py_pkg/b.py")
    assert "py_pkg/a.py" in imports

    # same-file: Helper.shout -> greet
    callees_or_callers = callers_of(graph, "py_pkg/a.py::greet")
    assert any("shout" in c or "Helper" in c for c in callees_or_callers) or any(
        e.callee.endswith("::greet") and "shout" in e.caller for e in graph.call_edges
    )


def test_python_from_import_alias_call_edge():
    """`from x import login as auth_login` must create a call edge to original `login`."""
    parser = AstParser()
    files = {
        "svc.py": "def login(u: str) -> str:\n    return u\n",
        "api.py": (
            "from svc import login as auth_login\n\n"
            "def login(u: str) -> str:\n    return auth_login(u)\n"
        ),
    }
    definitions_by_file = {
        path: parser.parse_definitions(content, "python") for path, content in files.items()
    }
    graph = DependencyGraphBuilder().build(
        repo_id="alias",
        commit_hash=None,
        files=files,
        definitions_by_file=definitions_by_file,
    )
    assert any(
        e.caller.endswith("api.py::login") and e.callee.endswith("svc.py::login")
        for e in graph.call_edges
    ), graph.call_edges
    assert "svc.py::login" in callees_of(graph, "api.py::login")


def test_js_relative_import_edge():
    parser = AstParser()
    files = {
        "js_pkg/util.js": (SAMPLE_REPO / "js_pkg/util.js").read_text(encoding="utf-8"),
        "js_pkg/main.ts": (SAMPLE_REPO / "js_pkg/main.ts").read_text(encoding="utf-8"),
    }
    definitions_by_file = {
        "js_pkg/util.js": parser.parse_definitions(files["js_pkg/util.js"], "javascript"),
        "js_pkg/main.ts": parser.parse_definitions(files["js_pkg/main.ts"], "typescript"),
    }
    graph = DependencyGraphBuilder().build(
        repo_id="test",
        commit_hash=None,
        files=files,
        definitions_by_file=definitions_by_file,
    )
    assert "js_pkg/util.js" in file_imports(graph, "js_pkg/main.ts")


def test_java_field_method_cross_file_call():
    """authService.login() should link AuthController.login → AuthService.login."""
    parser = AstParser()
    files = {
        "auth/AuthController.java": (SPRING / "auth/AuthController.java").read_text(
            encoding="utf-8"
        ),
        "auth/AuthService.java": (SPRING / "auth/AuthService.java").read_text(encoding="utf-8"),
        "user/UserRepository.java": (SPRING / "user/UserRepository.java").read_text(
            encoding="utf-8"
        ),
    }
    definitions_by_file = {
        path: parser.parse_definitions(content, "java") for path, content in files.items()
    }
    graph = DependencyGraphBuilder().build(
        repo_id="java-field",
        commit_hash=None,
        files=files,
        definitions_by_file=definitions_by_file,
    )

    ctrl_login = "auth/AuthController.java::AuthController.login"
    svc_login = "auth/AuthService.java::AuthService.login"
    find = "user/UserRepository.java::UserRepository.findByUsername"

    assert svc_login in callees_of(graph, ctrl_login) or any(
        e.caller == ctrl_login and e.callee == svc_login for e in graph.call_edges
    ), graph.call_edges
    assert find in callees_of(graph, svc_login) or any(
        e.caller == svc_login and e.callee == find for e in graph.call_edges
    ), graph.call_edges
