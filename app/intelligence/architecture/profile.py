"""Repository Profile: languages, frameworks, build systems, infra clues.

Heuristic scan of KnowledgeGraph + optional workspace manifest files.
No LLM. Evidence-backed hits only.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.intelligence.architecture.models import (
    EvidenceRef,
    EvidenceRefKind,
    FrameworkHit,
    InfraHit,
    InfraKind,
    ModuleMap,
    RepositoryProfile,
)
from app.intelligence.models import KnowledgeGraph, NodeKind

# Manifest / lockfile → build system
_BUILD_FILES: dict[str, str] = {
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "settings.gradle": "gradle",
    "settings.gradle.kts": "gradle",
    "package.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "pyproject.toml": "poetry_or_pep621",
    "requirements.txt": "pip",
    "Pipfile": "pipenv",
    "go.mod": "go_modules",
    "Cargo.toml": "cargo",
    "Gemfile": "bundler",
    "composer.json": "composer",
}

# Keyword → framework (scanned in manifest text + filenames)
_FRAMEWORK_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("Spring Boot", re.compile(r"spring-boot|springframework", re.I), "high"),
    ("Spring", re.compile(r"\bspring\b", re.I), "medium"),
    ("FastAPI", re.compile(r"\bfastapi\b", re.I), "high"),
    ("Django", re.compile(r"\bdjango\b", re.I), "high"),
    ("Flask", re.compile(r"\bflask\b", re.I), "high"),
    ("Express", re.compile(r"\bexpress\b", re.I), "high"),
    ("Next.js", re.compile(r"\bnext\b", re.I), "medium"),
    ("React", re.compile(r"\breact\b", re.I), "medium"),
    ("NestJS", re.compile(r"\b@nestjs\b|\bnestjs\b", re.I), "high"),
    ("Gin", re.compile(r"\bgin-gonic\b|\bgithub.com/gin-gonic", re.I), "high"),
]

_INFRA_RULES: list[tuple[InfraKind, str, re.Pattern[str], str]] = [
    (InfraKind.DATABASE, "PostgreSQL", re.compile(r"postgres|postgresql|psycopg", re.I), "high"),
    (InfraKind.DATABASE, "MySQL", re.compile(r"\bmysql\b|mariadb", re.I), "high"),
    (InfraKind.DATABASE, "MongoDB", re.compile(r"\bmongo\b|mongodb", re.I), "high"),
    (InfraKind.DATABASE, "SQLite", re.compile(r"\bsqlite\b", re.I), "medium"),
    (InfraKind.CACHE, "Redis", re.compile(r"\bredis\b", re.I), "high"),
    (InfraKind.MQ, "Kafka", re.compile(r"\bkafka\b", re.I), "high"),
    (InfraKind.MQ, "RabbitMQ", re.compile(r"\brabbit\b|amqp", re.I), "high"),
    (InfraKind.SEARCH, "Elasticsearch", re.compile(r"elasticsearch|opensearch", re.I), "high"),
    (InfraKind.CLOUD, "AWS", re.compile(r"\baws-sdk\b|\bboto3\b|amazonaws", re.I), "medium"),
]

_ENTRY_FILE_NAMES = frozenset(
    {
        "main.py",
        "app.py",
        "application.py",
        "manage.py",
        "main.go",
        "main.ts",
        "main.js",
        "index.ts",
        "index.js",
    }
)

_ENTRY_CLASS_HINT = re.compile(r"(Application|App|Main|Bootstrap)$")


def build_repository_profile(
    graph: KnowledgeGraph,
    *,
    workspace_root: Path | str | None = None,
    module_map: ModuleMap | None = None,
) -> RepositoryProfile:
    """
    Build a static repository profile.

    ``workspace_root`` enables manifest/dependency keyword scanning.
    Without it, profile still covers languages/symbols from the KG.
    """
    languages = _language_counts(graph)
    file_count = sum(1 for n in graph.nodes if n.kind == NodeKind.FILE)
    if file_count == 0:
        file_count = len({n.file_path for n in graph.nodes if n.file_path})
    symbol_count = sum(1 for n in graph.nodes if n.kind != NodeKind.FILE)

    build_systems: list[str] = []
    frameworks: list[FrameworkHit] = []
    infra: list[InfraHit] = []
    evidence: list[EvidenceRef] = []
    entrypoints = _detect_entrypoints(graph)

    root = Path(workspace_root) if workspace_root else None
    if root and root.is_dir():
        manifests = _iter_manifest_files(root)
        for rel, abs_path in manifests:
            text = _safe_read(abs_path)
            name = Path(rel).name
            if name in _BUILD_FILES:
                sys_name = _BUILD_FILES[name]
                if sys_name not in build_systems:
                    build_systems.append(sys_name)
                evidence.append(
                    EvidenceRef(
                        kind=EvidenceRefKind.FILE,
                        file_path=rel,
                        note=f"build_system:{sys_name}",
                    )
                )
            if not text:
                continue
            for fw_name, pattern, conf in _FRAMEWORK_RULES:
                if pattern.search(text) and not any(f.name == fw_name for f in frameworks):
                    frameworks.append(
                        FrameworkHit(
                            name=fw_name,
                            confidence=conf,  # type: ignore[arg-type]
                            evidence=[
                                EvidenceRef(
                                    kind=EvidenceRefKind.FILE,
                                    file_path=rel,
                                    note=f"keyword match for {fw_name}",
                                )
                            ],
                        )
                    )
            for kind, iname, pattern, conf in _INFRA_RULES:
                if pattern.search(text) and not any(
                    i.name == iname and i.kind == kind for i in infra
                ):
                    infra.append(
                        InfraHit(
                            kind=kind,
                            name=iname,
                            confidence=conf,  # type: ignore[arg-type]
                            evidence=[
                                EvidenceRef(
                                    kind=EvidenceRefKind.FILE,
                                    file_path=rel,
                                    note=f"keyword match for {iname}",
                                )
                            ],
                        )
                    )

        # docker-compose / k8s hints
        for rel, abs_path in _iter_infra_config_files(root):
            text = _safe_read(abs_path)
            if not text:
                continue
            for kind, iname, pattern, conf in _INFRA_RULES:
                if pattern.search(text) and not any(
                    i.name == iname and i.kind == kind for i in infra
                ):
                    infra.append(
                        InfraHit(
                            kind=kind,
                            name=iname,
                            confidence="medium" if conf == "high" else conf,  # type: ignore[arg-type]
                            evidence=[
                                EvidenceRef(
                                    kind=EvidenceRefKind.FILE,
                                    file_path=rel,
                                    note=f"config hint for {iname}",
                                )
                            ],
                        )
                    )

    # Language-only evidence if no manifests
    if not evidence and languages:
        top_lang = max(languages.items(), key=lambda kv: kv[1])[0]
        sample = next(
            (n.file_path for n in graph.nodes if n.language == top_lang and n.file_path),
            None,
        )
        if sample:
            evidence.append(
                EvidenceRef(
                    kind=EvidenceRefKind.FILE,
                    file_path=sample,
                    note=f"dominant_language:{top_lang}",
                )
            )

    # Path-based framework hints from KG (e.g. FastAPI-style app/api without deps file)
    frameworks = _augment_frameworks_from_graph(graph, frameworks)

    return RepositoryProfile(
        languages=languages,
        frameworks=frameworks,
        build_systems=build_systems,
        infra=infra,
        entrypoints=entrypoints,
        file_count=file_count,
        symbol_count=symbol_count,
        module_count=len(module_map.modules) if module_map else 0,
        evidence=evidence,
        meta={"workspace_scanned": bool(root and root.is_dir())},
    )


def _language_counts(graph: KnowledgeGraph) -> dict[str, int]:
    counts: dict[str, int] = {}
    seen_files: set[str] = set()
    for n in graph.nodes:
        if not n.language or not n.file_path:
            continue
        fp = n.file_path.replace("\\", "/")
        if fp in seen_files:
            continue
        seen_files.add(fp)
        counts[n.language] = counts.get(n.language, 0) + 1
    return counts


def _detect_entrypoints(graph: KnowledgeGraph) -> list[str]:
    hits: list[str] = []
    for n in graph.nodes:
        if not n.file_path:
            continue
        name = Path(n.file_path.replace("\\", "/")).name.lower()
        if name in _ENTRY_FILE_NAMES:
            hits.append(n.file_path.replace("\\", "/"))
        if n.kind.value == "class" and _ENTRY_CLASS_HINT.search(n.name):
            hits.append(n.qualified_name)
        if n.kind.value == "function" and n.name in {"main", "create_app", "app"}:
            hits.append(n.qualified_name)
    # unique preserve order
    return list(dict.fromkeys(hits))[:20]


def _augment_frameworks_from_graph(
    graph: KnowledgeGraph,
    existing: list[FrameworkHit],
) -> list[FrameworkHit]:
    out = list(existing)
    names = {f.name for f in out}
    paths = " ".join(n.file_path or "" for n in graph.nodes).lower()
    # Spring-ish package layout without pom in workspace scan
    if "spring" not in names and any(
        "application.java" in (n.file_path or "").lower()
        or n.name.endswith("Application")
        for n in graph.nodes
    ):
        sample = next(
            (
                n.file_path
                for n in graph.nodes
                if n.file_path and "application" in n.file_path.lower()
            ),
            None,
        )
        if sample and "Spring" not in names:
            out.append(
                FrameworkHit(
                    name="Spring",
                    confidence="low",
                    evidence=[
                        EvidenceRef(
                            kind=EvidenceRefKind.FILE,
                            file_path=sample,
                            note="Application class / path heuristic",
                        )
                    ],
                )
            )
    if "FastAPI" not in names and ("/api/" in paths or "fastapi" in paths):
        # weak — only if python dominant
        langs = _language_counts(graph)
        if langs.get("python", 0) > 0:
            sample = next(
                (n.file_path for n in graph.nodes if n.file_path and "/api/" in n.file_path),
                None,
            )
            if sample:
                out.append(
                    FrameworkHit(
                        name="FastAPI",
                        confidence="low",
                        evidence=[
                            EvidenceRef(
                                kind=EvidenceRefKind.FILE,
                                file_path=sample,
                                note="python api/ path heuristic (unconfirmed without deps)",
                            )
                        ],
                    )
                )
    return out


def _iter_manifest_files(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    skip = {".git", "node_modules", ".venv", "venv", "target", "dist", "__pycache__"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(p in skip for p in path.parts):
            continue
        if path.name in _BUILD_FILES or path.name in {
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yaml",
        }:
            rel = path.relative_to(root).as_posix()
            out.append((rel, path))
        if len(out) >= 80:
            break
    return out


def _iter_infra_config_files(root: Path) -> list[tuple[str, Path]]:
    names = {
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yaml",
        "application.yml",
        "application.yaml",
        "application.properties",
        ".env.example",
    }
    out: list[tuple[str, Path]] = []
    skip = {".git", "node_modules", ".venv", "venv", "target"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(p in skip for p in path.parts):
            continue
        if path.name in names:
            out.append((path.relative_to(root).as_posix(), path))
        if len(out) >= 40:
            break
    return out


def _safe_read(path: Path, limit: int = 200_000) -> str:
    try:
        data = path.read_bytes()[:limit]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""
