"""Module Discovery: path clustering + role mix + cohesion.

Produces architectural *regions* (clusters), not guaranteed domain modules.
``module_type`` / ``boundary_confidence`` keep that distinction explicit.
"""

from __future__ import annotations

from collections import defaultdict

from app.intelligence.architecture.models import (
    ArchitectureModule,
    EvidenceRef,
    EvidenceRefKind,
    ModuleMap,
    ModuleType,
)
from app.intelligence.enrichers.roles import (
    FlowRole,
    RoleIndex,
    build_role_index,
    role_of,
)
from app.intelligence.flow_topics import TOPIC_LEXICON
from app.intelligence.models import EdgeType, KnowledgeGraph, NodeKind
from app.intelligence.query import get_node

# Skip these when choosing cluster keys (descend one level if possible)
_NOISE_SEGMENTS = frozenset(
    {
        "src",
        "main",
        "java",
        "kotlin",
        "scala",
        "resources",
        "test",
        "tests",
        "python",
        "__pycache__",
        "node_modules",
        "target",
        "build",
        "dist",
        "out",
        "bin",
        "lib",
        "libs",
        "vendor",
        "com",
        "org",
        "net",
        "io",  # overly generic Java roots — still allow com.xxx.project later via depth
    }
)

_LAYER_NAMES = frozenset(
    {
        "controller",
        "controllers",
        "service",
        "services",
        "repository",
        "repositories",
        "repo",
        "repos",
        "dao",
        "mapper",
        "mappers",
        "api",
        "apis",
        "web",
        "rest",
        "handlers",
        "handler",
        "routes",
        "endpoint",
        "endpoints",
        "persistence",
        "infra",
        "infrastructure",
        "gateway",
        "middleware",
        "filter",
        "filters",
    }
)

_TECHNICAL_NAMES = frozenset(
    {
        "util",
        "utils",
        "common",
        "shared",
        "config",
        "configuration",
        "configs",
        "helper",
        "helpers",
        "constant",
        "constants",
        "exception",
        "exceptions",
        "error",
        "errors",
        "dto",
        "vos",
        "vo",
        "bean",
        "beans",
        "model",
        "models",
        "entity",
        "entities",
        "misc",
        "internal",
        "support",
    }
)

_MIN_CLUSTER_FILES = 1


def discover_modules(
    graph: KnowledgeGraph,
    *,
    role_index: RoleIndex | None = None,
    min_files: int = _MIN_CLUSTER_FILES,
) -> ModuleMap:
    """
    Cluster files by path prefix, then label type/responsibility/cohesion.
    """
    roles = role_index or build_role_index(graph)
    files = _collect_files(graph)
    if not files:
        return ModuleMap(modules=[], unresolved_files=[], method="path_cluster+role+cohesion")

    clusters: dict[str, list[str]] = defaultdict(list)
    unresolved: list[str] = []
    for path in files:
        key = cluster_key_for_path(path)
        if key is None:
            unresolved.append(path)
            continue
        clusters[key].append(path)

    # Merge tiny clusters into parent when possible
    clusters = _merge_small_clusters(clusters, min_files=max(1, min_files))

    file_to_module = {f: key for key, paths in clusters.items() for f in paths}
    cohesion_map = _compute_cohesion(graph, file_to_module)

    modules: list[ArchitectureModule] = []
    for key, paths in sorted(clusters.items(), key=lambda kv: kv[0]):
        paths = sorted(set(paths))
        symbol_ids = [
            n.id
            for n in graph.nodes
            if n.kind != NodeKind.FILE and n.file_path in paths
        ]
        role_mix = _role_mix(graph, paths, roles)
        mtype, bconf = classify_module_type(key, role_mix, paths)
        responsibility = infer_responsibility(key, role_mix)
        mid = f"mod:{key.replace('/', '.')}"
        evidence = [
            EvidenceRef(
                kind=EvidenceRefKind.MODULE,
                module_id=mid,
                note=f"path cluster root '{key}/' ({len(paths)} files)",
            ),
            EvidenceRef(
                kind=EvidenceRefKind.FILE,
                file_path=paths[0],
                note="representative file in cluster",
            ),
        ]
        modules.append(
            ArchitectureModule(
                id=mid,
                name=key.split("/")[-1],
                path_roots=[key],
                module_type=mtype,
                boundary_confidence=bconf,
                responsibility=responsibility,
                role_mix=role_mix,
                file_paths=paths,
                symbol_ids=symbol_ids,
                evidence=evidence,
                cohesion=cohesion_map.get(key),
                meta={"cluster_key": key, "file_count": len(paths)},
            )
        )

    return ModuleMap(
        modules=modules,
        unresolved_files=sorted(unresolved),
        method="path_cluster+role+cohesion",
    )


def cluster_key_for_path(file_path: str) -> str | None:
    """
    Choose a stable directory cluster key.

    Prefer the first non-noise segment (feature or layer folder).
    For ``app/services/auth_service.py`` → ``app/services`` when ``app`` is shallow.
    """
    posix = file_path.replace("\\", "/")
    parts = [p for p in posix.split("/") if p and p != "."]
    if len(parts) < 2:
        # top-level file → unresolved or single-segment parent
        return parts[0] if len(parts) == 1 and _is_dir_like_name(parts[0]) else None

    dirs = parts[:-1]  # drop filename
    # Strip leading noise (src/main/java/com/...)
    i = 0
    while i < len(dirs) and dirs[i].lower() in _NOISE_SEGMENTS:
        i += 1
    useful = dirs[i:]
    if not useful:
        # everything was noise — use last dir before file
        return dirs[-1] if dirs else None

    # If first useful is generic app/ and more remains, take two levels
    first = useful[0].lower()
    if first in {"app", "application", "project"} and len(useful) >= 2:
        return f"{useful[0]}/{useful[1]}"

    # Java-style com.company.project.module — skip company-like if deep
    if first in {"com", "org", "net"} and len(useful) >= 3:
        # com/foo/bar/module → bar/module or just module?
        # Prefer last two meaningful: project.module style → useful[-2]/useful[-1] if depth high
        if len(useful) >= 4:
            return f"{useful[-2]}/{useful[-1]}"
        return useful[-1]

    return useful[0]


def classify_module_type(
    cluster_key: str,
    role_mix: dict[str, int],
    file_paths: list[str],
) -> tuple[ModuleType, str]:
    """Return (module_type, boundary_confidence)."""
    name = cluster_key.split("/")[-1].lower()
    leaf = name

    if leaf in _LAYER_NAMES or any(seg.lower() in _LAYER_NAMES for seg in cluster_key.split("/")):
        # Pure technical layer folder (controller/, services/)
        return ModuleType.LAYER, "medium"

    if leaf in _TECHNICAL_NAMES:
        return ModuleType.TECHNICAL, "medium"

    roles_present = {k for k, v in role_mix.items() if v > 0}
    layered_roles = {
        FlowRole.CONTROLLER.value,
        FlowRole.SERVICE.value,
        FlowRole.REPOSITORY.value,
    }
    # Feature-like: business name + mixed roles or topic hit
    topic_hit = _topic_hit(leaf)
    mixed = len(roles_present & layered_roles) >= 2

    if topic_hit and mixed:
        return ModuleType.FEATURE, "high"
    if topic_hit:
        return ModuleType.FEATURE, "medium"
    if mixed and leaf not in _LAYER_NAMES:
        return ModuleType.FEATURE, "medium"
    if roles_present == {FlowRole.REPOSITORY.value} and leaf not in _LAYER_NAMES:
        return ModuleType.FEATURE, "low"
    if not roles_present or roles_present == {FlowRole.UNKNOWN.value}:
        return ModuleType.UNKNOWN, "low"
    return ModuleType.UNKNOWN, "low"


def infer_responsibility(cluster_key: str, role_mix: dict[str, int]) -> str:
    leaf = cluster_key.split("/")[-1].lower()
    topic = _topic_hit(leaf)
    if topic:
        return topic

    # role-dominant label
    if role_mix:
        dominant = max(role_mix.items(), key=lambda kv: kv[1])[0]
        if dominant == FlowRole.CONTROLLER.value:
            return "http_api"
        if dominant == FlowRole.SERVICE.value:
            return "application_service"
        if dominant == FlowRole.REPOSITORY.value:
            return "persistence"
        if dominant == FlowRole.MQ.value:
            return "messaging"
        if dominant == FlowRole.CACHE.value:
            return "caching"

    if leaf in _LAYER_NAMES:
        return f"layer:{leaf}"
    if leaf in _TECHNICAL_NAMES:
        return f"technical:{leaf}"
    return f"module:{leaf}"


def _topic_hit(name: str) -> str | None:
    n = name.lower().replace("-", "_")
    for spec in TOPIC_LEXICON:
        for term in spec.terms:
            t = term.lower().replace("-", "_")
            if t.isascii() and (t == n or t in n or n in t):
                return spec.topic
    # common domain folder names not in lexicon
    aliases = {
        "auth": "login",
        "user": "user",
        "users": "user",
        "order": "order_create",
        "orders": "order_create",
        "payment": "payment",
        "pay": "payment",
    }
    return aliases.get(n)


def _collect_files(graph: KnowledgeGraph) -> list[str]:
    paths: set[str] = set()
    for n in graph.nodes:
        if n.kind == NodeKind.FILE and n.file_path:
            paths.add(n.file_path.replace("\\", "/"))
        elif n.file_path:
            paths.add(n.file_path.replace("\\", "/"))
    return sorted(paths)


def _is_dir_like_name(name: str) -> bool:
    return bool(name) and "." not in name


def _merge_small_clusters(
    clusters: dict[str, list[str]],
    *,
    min_files: int,
) -> dict[str, list[str]]:
    if min_files <= 1:
        return {k: sorted(set(v)) for k, v in clusters.items()}
    # v1: keep all clusters; merging across unrelated keys is risky
    return {k: sorted(set(v)) for k, v in clusters.items() if len(set(v)) >= min_files or True}


def _role_mix(
    graph: KnowledgeGraph,
    file_paths: list[str],
    roles: RoleIndex,
) -> dict[str, int]:
    path_set = set(file_paths)
    mix: dict[str, int] = defaultdict(int)
    for n in graph.nodes:
        if n.kind == NodeKind.FILE:
            continue
        if n.file_path not in path_set:
            continue
        r = role_of(roles, n.id)
        mix[r.value] += 1
    return dict(mix)


def _compute_cohesion(
    graph: KnowledgeGraph,
    file_to_module: dict[str, str],
) -> dict[str, float]:
    """cohesion(m) = internal edges / (internal + external edges) touching m."""
    internal: dict[str, int] = defaultdict(int)
    external: dict[str, int] = defaultdict(int)

    def file_of(node_id: str) -> str | None:
        n = get_node(graph, node_id)
        if n is None:
            return None
        if n.kind == NodeKind.FILE:
            return n.file_path
        return n.file_path

    for e in graph.edges:
        if e.edge_type not in {EdgeType.CALL, EdgeType.IMPORT}:
            continue
        sf = file_of(e.source_id)
        tf = file_of(e.target_id)
        if not sf or not tf:
            continue
        sm = file_to_module.get(sf.replace("\\", "/"))
        tm = file_to_module.get(tf.replace("\\", "/"))
        if sm is None and tm is None:
            continue
        if sm is not None and sm == tm:
            internal[sm] += 1
        else:
            if sm is not None:
                external[sm] += 1
            if tm is not None and tm != sm:
                external[tm] += 1

    out: dict[str, float] = {}
    for key in set(internal) | set(external) | set(file_to_module.values()):
        inn = internal.get(key, 0)
        ext = external.get(key, 0)
        total = inn + ext
        out[key] = (inn / total) if total else 1.0
    return out
