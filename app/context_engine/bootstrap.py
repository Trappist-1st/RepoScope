"""Repository Bootstrap Context: the four-part "launchpad" an agent should see first.

Assembles README + repository profile + top-coupling module map + a handful of
core source excerpts (entrypoints / high-coupling modules), so an Agent gets a
global orientation before drilling in with search_code / view_source /
query_dependencies. Pure function over an already-built ArchitectureReport --
no LLM calls, no network I/O beyond reading files already checked out locally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.intelligence.architecture.models import ArchitectureModule, ArchitectureReport
from app.intelligence.models import KnowledgeGraph
from app.intelligence.query import find_by_qualified_name

README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md", "README")
README_CHAR_LIMIT = 8000
CORE_FILE_LINE_LIMIT = 150
CORE_FILE_CHAR_LIMIT = 6000


@dataclass
class CoreFileExcerpt:
    file_path: str
    content: str
    truncated: bool
    reason: str  # "entrypoint" | "high_coupling_module"


@dataclass
class BootstrapModuleSummary:
    id: str
    name: str
    module_type: str
    responsibility: str
    boundary_confidence: str
    file_count: int
    path_roots: list[str]
    coupling: float = 0.0


@dataclass
class BootstrapContext:
    readme_path: str | None
    readme_excerpt: str
    readme_truncated: bool
    languages: dict[str, int]
    frameworks: list[str]
    build_systems: list[str]
    infra: list[str]
    entrypoints: list[str]
    core_modules: list[BootstrapModuleSummary]
    core_files: list[CoreFileExcerpt]
    remaining_modules: list[BootstrapModuleSummary]
    warnings: list[str] = field(default_factory=list)


def assemble_bootstrap_context(
    graph: KnowledgeGraph,
    arch: ArchitectureReport,
    *,
    workspace_root: Path | str,
    top_k_modules: int = 8,
    top_k_core_files: int = 5,
) -> BootstrapContext:
    root = Path(workspace_root)
    warnings: list[str] = []

    readme_path, readme_excerpt, readme_truncated = _read_readme(root)
    if readme_path is None:
        warnings.append("no README found at workspace root")

    per_module = arch.metrics.per_module
    modules_ranked = sorted(
        arch.modules.modules,
        key=lambda m: per_module.get(m.id, {}).get("coupling", 0.0),
        reverse=True,
    )

    def to_summary(m: ArchitectureModule) -> BootstrapModuleSummary:
        return BootstrapModuleSummary(
            id=m.id,
            name=m.name,
            module_type=m.module_type.value,
            responsibility=m.responsibility,
            boundary_confidence=m.boundary_confidence,
            file_count=len(m.file_paths),
            path_roots=list(m.path_roots),
            coupling=per_module.get(m.id, {}).get("coupling", 0.0),
        )

    core_modules = [to_summary(m) for m in modules_ranked[:top_k_modules]]
    core_module_ids = {m.id for m in core_modules}
    remaining_modules = [
        to_summary(m) for m in arch.modules.modules if m.id not in core_module_ids
    ]

    core_files = _pick_core_files(
        graph,
        arch,
        modules_ranked,
        root=root,
        limit=max(0, top_k_core_files),
    )
    if not core_files:
        warnings.append("no core file source could be read (workspace files missing?)")

    return BootstrapContext(
        readme_path=readme_path,
        readme_excerpt=readme_excerpt,
        readme_truncated=readme_truncated,
        languages=dict(arch.profile.languages),
        frameworks=[f.name for f in arch.profile.frameworks],
        build_systems=list(arch.profile.build_systems),
        infra=[i.name for i in arch.profile.infra],
        entrypoints=list(arch.profile.entrypoints),
        core_modules=core_modules,
        core_files=core_files,
        remaining_modules=remaining_modules,
        warnings=warnings,
    )


def _pick_core_files(
    graph: KnowledgeGraph,
    arch: ArchitectureReport,
    modules_ranked: list[ArchitectureModule],
    *,
    root: Path,
    limit: int,
) -> list[CoreFileExcerpt]:
    if limit <= 0:
        return []

    candidates: list[tuple[str, str]] = []  # (file_path, reason)
    seen: set[str] = set()

    for entry in arch.profile.entrypoints:
        fp = _resolve_entry_file(entry, graph)
        if fp and fp not in seen:
            seen.add(fp)
            candidates.append((fp, "entrypoint"))
        if len(candidates) >= limit:
            break

    if len(candidates) < limit:
        for m in modules_ranked:
            if len(candidates) >= limit:
                break
            if not m.file_paths:
                continue
            fp = m.file_paths[0].replace("\\", "/")
            if fp in seen:
                continue
            seen.add(fp)
            candidates.append((fp, "high_coupling_module"))

    excerpts: list[CoreFileExcerpt] = []
    for fp, reason in candidates[:limit]:
        content, truncated = _read_file_excerpt(root, fp)
        if not content:
            continue
        excerpts.append(CoreFileExcerpt(file_path=fp, content=content, truncated=truncated, reason=reason))
    return excerpts


def _resolve_entry_file(entry: str, graph: KnowledgeGraph) -> str | None:
    normalized = entry.replace("\\", "/")
    tail = normalized.rsplit("/", 1)[-1]
    if "/" in normalized and "." in tail:
        return normalized
    node = find_by_qualified_name(graph, entry)
    if node is not None and node.file_path:
        return node.file_path.replace("\\", "/")
    return None


def _read_readme(workspace_root: Path) -> tuple[str | None, str, bool]:
    for name in README_NAMES:
        p = workspace_root / name
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        text = raw[: README_CHAR_LIMIT + 1].decode("utf-8", errors="replace")
        truncated = len(text) > README_CHAR_LIMIT
        return name, text[:README_CHAR_LIMIT], truncated
    return None, "", False


def _read_file_excerpt(workspace_root: Path, file_path: str) -> tuple[str, bool]:
    abs_path = workspace_root / file_path
    try:
        raw = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", False
    lines = raw.splitlines()
    truncated = len(lines) > CORE_FILE_LINE_LIMIT or len(raw) > CORE_FILE_CHAR_LIMIT
    excerpt = "\n".join(lines[:CORE_FILE_LINE_LIMIT])[:CORE_FILE_CHAR_LIMIT]
    return excerpt, truncated

