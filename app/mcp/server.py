"""RepoScope MCP Server (stdio).

Run:
  python -m app.mcp.server

Configure in Claude Code / Cursor / MCP Inspector — see docs/mcp_setup.md
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

from app.mcp.service import RepoScopeFacade

mcp = FastMCP(
    "reposcope",
    instructions=(
        "RepoScope exposes repository intelligence tools with citation-backed evidence. "
        "Always inspect meta.warnings — especially audit_backend in_memory notices."
    ),
)

_facade: RepoScopeFacade | None = None


def get_facade() -> RepoScopeFacade:
    global _facade
    if _facade is None:
        _facade = RepoScopeFacade(use_hash_embedder=True)
    return _facade


@mcp.tool()
def get_repo_summary(
    repo_url: str,
    question: str | None = None,
    force_reindex: bool = False,
) -> dict:
    """Return a citation-backed architecture summary for a Git repo URL or local path."""
    result = get_facade().get_repo_summary(
        repo_url=repo_url,
        question=question,
        force_reindex=force_reindex,
    )
    return result.model_dump()


@mcp.tool()
def query_dependencies(
    repo_url: str,
    symbol_name: str,
    direction: Literal["both", "callers", "callees", "imports"] = "both",
    limit: int = 20,
    force_reindex: bool = False,
) -> dict:
    """Query callers/callees/imports for a symbol. Prefer file::symbol when ambiguous."""
    result = get_facade().query_dependencies(
        repo_url=repo_url,
        symbol_name=symbol_name,
        direction=direction,
        limit=limit,
        force_reindex=force_reindex,
    )
    return result.model_dump()


@mcp.tool()
def suggest_refactor(
    repo_url: str,
    file_path: str,
    focus: str | None = None,
    max_suggestions: int = 5,
    force_reindex: bool = False,
) -> dict:
    """Suggest refactors for a file with citation-backed evidence."""
    result = get_facade().suggest_refactor(
        repo_url=repo_url,
        file_path=file_path,
        focus=focus,
        max_suggestions=max_suggestions,
        force_reindex=force_reindex,
    )
    return result.model_dump()


@mcp.tool()
def trace_flow(
    repo_url: str,
    question: str,
    entry_hint: str | None = None,
    max_depth: int = 5,
    force_reindex: bool = False,
) -> dict:
    """Trace a business/code flow (e.g. login) with file:line evidence steps.

    Returns a structured FlowTrace: entry → call chain → optional synthetic
    database/MQ terminal. Does not modify code. Prefer concrete questions like
    "用户登录流程是什么？" or "how does order creation flow?".
    """
    result = get_facade().trace_flow(
        repo_url=repo_url,
        question=question,
        entry_hint=entry_hint,
        max_depth=max_depth,
        force_reindex=force_reindex,
    )
    return result.model_dump()


@mcp.tool()
def analyze_architecture(
    repo_url: str,
    force_reindex: bool = False,
) -> dict:
    """Analyze repository architecture: modules, patterns, profile, coupling.

    Returns an evidence-backed ArchitectureReport (not a chat narrative).
    Patterns are heuristic (layered/mvc/hexagonal/event_driven/unknown).
    Does not modify code and does not run FlowTracer by default.
    """
    result = get_facade().analyze_architecture(
        repo_url=repo_url,
        force_reindex=force_reindex,
        include_flows=False,
    )
    return result.model_dump()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
