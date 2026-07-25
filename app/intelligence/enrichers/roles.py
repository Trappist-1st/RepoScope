"""Heuristic role enrichment for KnowledgeGraph nodes.

Iteration 2 step 1: classify symbols/files into architectural roles
without static analysis or framework-specific ASTs.

Signals (first match wins, high → low specificity):
  1. Class / symbol name suffixes and keywords
  2. File path segments
  3. Optional source-text annotation / decorator hints
  4. Parent class role inheritance (method ← enclosing class)

Roles are attached via RoleIndex (node_id → FlowRole), not by mutating
KnowledgeGraph schema. Callers may optionally copy into node.meta["role"].
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from app.intelligence.models import KnowledgeGraph, KnowledgeNode, NodeKind


class FlowRole(str, Enum):
    CONTROLLER = "controller"
    SERVICE = "service"
    REPOSITORY = "repository"
    DATABASE = "database"
    CACHE = "cache"
    MQ = "mq"
    GATEWAY = "gateway"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


# node_id -> role
RoleIndex = dict[str, FlowRole]


# --- name patterns (checked against symbol / class name) ---

_CONTROLLER_NAME = re.compile(
    r"(Controller|Resource|RestController|ApiHandler|RequestHandler)$",
    re.I,
)
_GATEWAY_NAME = re.compile(r"(Gateway|Middleware|Filter|Interceptor)$", re.I)
_SERVICE_NAME = re.compile(r"(Service|UseCase|Interactor|Manager|Facade)$", re.I)
_REPOSITORY_NAME = re.compile(
    r"(Repository|Repo|Dao|Mapper|Store)$",
    re.I,
)
_CACHE_NAME = re.compile(r"(Cache|Redis|Caffeine)$", re.I)
_MQ_NAME = re.compile(
    r"(Producer|Consumer|Listener|Publisher|Subscriber|Kafka|Rabbit|MessageHandler)$",
    re.I,
)

# --- path segment hints ---

_CONTROLLER_PATH = re.compile(
    r"(^|/)(controller|controllers|web|api|rest|endpoints?|handlers?|routes?)(/|$)",
    re.I,
)
_SERVICE_PATH = re.compile(
    r"(^|/)(services?|domain|application|usecases?)(/|$)",
    re.I,
)
_REPOSITORY_PATH = re.compile(
    r"(^|/)(repositories?|repos?|dao|persistence|infra|infrastructure|mapper)(/|$)",
    re.I,
)
_GATEWAY_PATH = re.compile(r"(^|/)(gateway|filter|middleware)(/|$)", re.I)
_CACHE_PATH = re.compile(r"(^|/)(cache|redis)(/|$)", re.I)
_MQ_PATH = re.compile(r"(^|/)(messaging|mq|kafka|rabbit|events?)(/|$)", re.I)

# --- optional source hints (cheap substring / regex on file text) ---

_JAVA_CONTROLLER_ANN = re.compile(
    r"@(RestController|Controller|RequestMapping|GetMapping|PostMapping)\b"
)
_JAVA_REPO_ANN = re.compile(r"@(Repository|Mapper)\b")
_JAVA_SERVICE_ANN = re.compile(r"@Service\b")
_PY_ROUTER = re.compile(
    r"\b(APIRouter|FastAPI)\b|@(?:app|router)\.(get|post|put|delete|patch)\b",
    re.I,
)


def infer_role(
    node: KnowledgeNode,
    *,
    file_text: str | None = None,
    parent_role: FlowRole | None = None,
) -> FlowRole:
    """Infer a single node's role. Does not look up the graph."""
    if node.kind == NodeKind.FILE:
        return _role_from_path(node.file_path or node.name) or FlowRole.UNKNOWN

    # Methods/functions: prefer parent class role when known and not unknown
    if node.kind == NodeKind.METHOD and parent_role and parent_role != FlowRole.UNKNOWN:
        # Still allow stronger local signals (e.g. method named publishEvent)
        local = _role_from_name(node.name)
        if local in {FlowRole.MQ, FlowRole.CACHE, FlowRole.DATABASE}:
            return local
        return parent_role

    by_name = _role_from_name(node.name)
    if by_name != FlowRole.UNKNOWN:
        return by_name

    # qualified_name may include Class.method — check class token
    if "::" in node.qualified_name:
        symbol_part = node.qualified_name.split("::", 1)[1]
        class_token = symbol_part.split(".", 1)[0]
        if class_token != node.name:
            by_class = _role_from_name(class_token)
            if by_class != FlowRole.UNKNOWN:
                return by_class

    by_path = _role_from_path(node.file_path)
    if by_path != FlowRole.UNKNOWN:
        return by_path

    if file_text:
        by_text = _role_from_file_text(file_text, node)
        if by_text != FlowRole.UNKNOWN:
            return by_text

    if parent_role and parent_role != FlowRole.UNKNOWN:
        return parent_role

    return FlowRole.UNKNOWN


def build_role_index(
    graph: KnowledgeGraph,
    file_texts: dict[str, str] | None = None,
) -> RoleIndex:
    """
    Build node_id → FlowRole for all nodes.

    Processing order: files → classes → functions/methods so methods can
    inherit enclosing class roles via parent_id.
    """
    texts = file_texts or {}
    index: RoleIndex = {}

    # Pass 1: files + classes + top-level functions
    for node in graph.nodes:
        if node.kind == NodeKind.METHOD:
            continue
        text = _file_text_for(node, texts)
        index[node.id] = infer_role(node, file_text=text)

    # Pass 2: methods (parent class role available)
    for node in graph.nodes:
        if node.kind != NodeKind.METHOD:
            continue
        parent_role = index.get(node.parent_id) if node.parent_id else None
        text = _file_text_for(node, texts)
        index[node.id] = infer_role(node, file_text=text, parent_role=parent_role)

    return index


def role_of(index: RoleIndex, node_id: str) -> FlowRole:
    return index.get(node_id, FlowRole.UNKNOWN)


def attach_roles_to_meta(graph: KnowledgeGraph, index: RoleIndex) -> KnowledgeGraph:
    """
    Optional helper: copy roles into node.meta['role'] (returns new graph).
    Does not change schema_version; safe for debugging / demos.
    """
    nodes = []
    for node in graph.nodes:
        role = index.get(node.id, FlowRole.UNKNOWN)
        meta = dict(node.meta)
        meta["role"] = role.value
        nodes.append(node.model_copy(update={"meta": meta}))
    return graph.model_copy(update={"nodes": nodes})


def _file_text_for(node: KnowledgeNode, texts: dict[str, str]) -> str | None:
    if not node.file_path:
        return None
    return texts.get(node.file_path) or texts.get(node.file_path.replace("\\", "/"))


def _role_from_name(name: str) -> FlowRole:
    if not name:
        return FlowRole.UNKNOWN
    # Order: more specific architectural layers first
    if _CONTROLLER_NAME.search(name):
        return FlowRole.CONTROLLER
    if _GATEWAY_NAME.search(name):
        return FlowRole.GATEWAY
    if _REPOSITORY_NAME.search(name):
        return FlowRole.REPOSITORY
    if _MQ_NAME.search(name):
        return FlowRole.MQ
    if _CACHE_NAME.search(name):
        return FlowRole.CACHE
    if _SERVICE_NAME.search(name):
        return FlowRole.SERVICE
    # verb-ish free functions often used as handlers
    lower = name.lower()
    if lower in {"login", "signin", "sign_in", "authenticate", "logout"}:
        # alone insufficient → unknown; entry discovery uses topic match
        return FlowRole.UNKNOWN
    return FlowRole.UNKNOWN


def _role_from_path(file_path: str | None) -> FlowRole:
    if not file_path:
        return FlowRole.UNKNOWN
    posix = file_path.replace("\\", "/")
    # strip filename for segment checks, but also test full path
    if _CONTROLLER_PATH.search(posix):
        return FlowRole.CONTROLLER
    if _GATEWAY_PATH.search(posix):
        return FlowRole.GATEWAY
    if _REPOSITORY_PATH.search(posix):
        return FlowRole.REPOSITORY
    if _MQ_PATH.search(posix):
        return FlowRole.MQ
    if _CACHE_PATH.search(posix):
        return FlowRole.CACHE
    if _SERVICE_PATH.search(posix):
        return FlowRole.SERVICE
    # filename stem fallback
    stem = Path(posix).stem
    return _role_from_name(stem)


def _role_from_file_text(text: str, node: KnowledgeNode) -> FlowRole:
    """Cheap annotation/decorator scan; scoped loosely to the whole file."""
    if node.language in {"java", None} or (node.file_path or "").endswith(".java"):
        if _JAVA_CONTROLLER_ANN.search(text):
            # Only apply to class-like nodes or files in controller-ish context
            if node.kind in {NodeKind.CLASS, NodeKind.FILE, NodeKind.METHOD}:
                return FlowRole.CONTROLLER
        if _JAVA_REPO_ANN.search(text) and node.kind in {
            NodeKind.CLASS,
            NodeKind.FILE,
            NodeKind.METHOD,
        }:
            return FlowRole.REPOSITORY
        if _JAVA_SERVICE_ANN.search(text) and node.kind in {
            NodeKind.CLASS,
            NodeKind.FILE,
            NodeKind.METHOD,
        }:
            return FlowRole.SERVICE
    if node.language in {"python", None} or (node.file_path or "").endswith(".py"):
        if _PY_ROUTER.search(text) and node.kind in {
            NodeKind.FUNCTION,
            NodeKind.METHOD,
            NodeKind.FILE,
        }:
            # File-level router → controller-ish; function may be route handler
            if node.kind == NodeKind.FILE or _looks_like_handler_name(node.name):
                return FlowRole.CONTROLLER
    return FlowRole.UNKNOWN


def _looks_like_handler_name(name: str) -> bool:
    lower = name.lower()
    return lower in {
        "login",
        "logout",
        "signin",
        "signup",
        "register",
        "create",
        "update",
        "delete",
        "list",
        "get",
        "post",
        "handler",
    } or lower.startswith(("get_", "post_", "put_", "delete_", "create_", "list_"))
