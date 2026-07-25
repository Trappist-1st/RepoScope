"""Tests for Repository Profile."""

from __future__ import annotations

from pathlib import Path

from app.intelligence.architecture.modules import discover_modules
from app.intelligence.architecture.profile import build_repository_profile
from app.intelligence.ids import symbol_ref_to_node_id
from app.intelligence.models import KnowledgeGraph, KnowledgeNode, NodeKind
from tests.helpers_flow import FASTAPI_LOGIN, ingest_fixture


def test_profile_languages_from_kg():
    graph = KnowledgeGraph(
        repo_id="p",
        nodes=[
            KnowledgeNode(
                id="file:a.py",
                kind=NodeKind.FILE,
                name="a.py",
                qualified_name="a.py",
                file_path="a.py",
                language="python",
            ),
            KnowledgeNode(
                id=symbol_ref_to_node_id("a.py::foo"),
                kind=NodeKind.FUNCTION,
                name="foo",
                qualified_name="a.py::foo",
                file_path="a.py",
                language="python",
            ),
        ],
    )
    profile = build_repository_profile(graph)
    assert profile.languages.get("python") == 1
    assert profile.symbol_count == 1
    assert profile.evidence  # language evidence fallback


def test_profile_reads_pyproject(tmp_path: Path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        """
[project]
name = "demo"
dependencies = ["fastapi>=0.100", "redis>=5.0", "psycopg2-binary"]
""",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")

    graph = KnowledgeGraph(
        repo_id="demo",
        nodes=[
            KnowledgeNode(
                id="file:app.py",
                kind=NodeKind.FILE,
                name="app.py",
                qualified_name="app.py",
                file_path="app.py",
                language="python",
            ),
            KnowledgeNode(
                id=symbol_ref_to_node_id("app.py::main"),
                kind=NodeKind.FUNCTION,
                name="main",
                qualified_name="app.py::main",
                file_path="app.py",
                language="python",
            ),
        ],
    )
    profile = build_repository_profile(graph, workspace_root=repo)
    assert "poetry_or_pep621" in profile.build_systems
    assert any(f.name == "FastAPI" for f in profile.frameworks)
    assert any(i.name == "Redis" for i in profile.infra)
    assert any(i.name == "PostgreSQL" for i in profile.infra)
    assert all(f.evidence for f in profile.frameworks)
    assert all(i.evidence for i in profile.infra)
    assert "app.py" in profile.entrypoints or "app.py::main" in profile.entrypoints


def test_profile_reads_pom_spring(tmp_path: Path):
    repo = tmp_path / "java"
    repo.mkdir()
    (repo / "pom.xml").write_text(
        """
<project>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.kafka</groupId>
      <artifactId>spring-kafka</artifactId>
    </dependency>
  </dependencies>
</project>
""",
        encoding="utf-8",
    )
    graph = KnowledgeGraph(repo_id="j", nodes=[], edges=[])
    profile = build_repository_profile(graph, workspace_root=repo)
    assert "maven" in profile.build_systems
    assert any(f.name == "Spring Boot" for f in profile.frameworks)
    assert any(i.name == "Kafka" for i in profile.infra)


def test_profile_with_fastapi_fixture(tmp_path: Path):
    _, kg = ingest_fixture(FASTAPI_LOGIN, tmp_path)
    mmap = discover_modules(kg)
    # workspace is the fixture dir (cloned/copied under workspace by ingest)
    # Use fixture path directly for manifest-less scan + KG heuristics
    profile = build_repository_profile(
        kg,
        workspace_root=FASTAPI_LOGIN,
        module_map=mmap,
    )
    assert profile.languages.get("python", 0) >= 1
    assert profile.module_count == len(mmap.modules)
    assert profile.file_count >= 1
