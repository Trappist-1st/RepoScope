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
        "RepoScope is a structure-aware code context engine. "
        "Prefer context_explore for how-it-works / edit-prep questions — "
        "one call returns seeds, must-read source, call paths, and blast radius. "
        "Always inspect meta.warnings and meta.graph_update_mode."
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
def analyze_impact(
    repo_url: str,
    symbol_name: str,
    depth: int = 2,
    direction: Literal["affected", "depends_on", "both"] = "both",
    limit: int = 50,
    force_reindex: bool = False,
) -> dict:
    """Impact analysis before editing: who is affected, and what the symbol depends on.

    - affected: transitive callers + subtypes (blast radius of a change)
    - depends_on: callees + super-types
    Prefer file::symbol when the short name is ambiguous.
    """
    result = get_facade().analyze_impact(
        repo_url=repo_url,
        symbol_name=symbol_name,
        depth=depth,
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


@mcp.tool()
def search_code(
    repo_url: str,
    query: str,
    top_k: int = 10,
    graph_expand: bool = False,
    force_reindex: bool = False,
) -> dict:
    """Hybrid (BM25 + vector) keyword/semantic search over indexed code chunks.

    Use this to find *candidate* files/symbols for a free-text query before
    drilling in with view_source or query_dependencies. If nothing ranks well,
    falls back to a diversity sample of indexed chunks (still cited).
    """
    result = get_facade().search_code(
        repo_url=repo_url,
        query=query,
        top_k=top_k,
        graph_expand=graph_expand,
        force_reindex=force_reindex,
    )
    return result.model_dump()


@mcp.tool()
def view_source(
    repo_url: str,
    file_path: str,
    symbol_name: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    force_reindex: bool = False,
) -> dict:
    """Read source code: by symbol, by line range, or the whole file.

    Precedence: symbol_name > (start_line, end_line) > whole file. When no
    symbol/line range is given, returns the file capped at ~400 lines plus an
    `outline` of top-level definitions (name/kind/line range) so you can pick
    a symbol_name for a follow-up call instead of reading the whole file.
    If truncated, check `next_start_line` / `total_lines` and re-call with
    start_line=next_start_line to continue reading.
    """
    result = get_facade().view_source(
        repo_url=repo_url,
        file_path=file_path,
        symbol_name=symbol_name,
        start_line=start_line,
        end_line=end_line,
        force_reindex=force_reindex,
    )
    return result.model_dump()


@mcp.tool()
def get_initial_context(
    repo_url: str,
    top_k_modules: int = 8,
    top_k_core_files: int = 5,
    force_reindex: bool = False,
) -> dict:
    """Repository "launchpad": README + profile + core modules + core file source.

    Call this FIRST when starting to explore an unfamiliar repository. Returns:
    README excerpt, detected languages/frameworks/build systems/infra,
    entrypoints, the top-coupling modules with a source excerpt each
    (`core_files`), and a path-only list of the remaining modules
    (`remaining_modules`) to drill into with search_code / view_source /
    query_dependencies. No LLM calls -- purely evidence-backed heuristics.
    """
    result = get_facade().get_initial_context(
        repo_url=repo_url,
        top_k_modules=top_k_modules,
        top_k_core_files=top_k_core_files,
        force_reindex=force_reindex,
    )
    return result.model_dump()


@mcp.tool()
def context_explore(
    repo_url: str,
    query: str,
    top_k: int = 8,
    blast_depth: int = 2,
    include_flow: bool | None = None,
    force_reindex: bool = False,
) -> dict:
    """Primary tool: one call returns surgical coding context.

    Returns seed symbols with snippets (must_read), call paths (FlowTracer when
    the question looks like a flow), and blast radius (callers/callees/inherit).
    Prefer this over chaining search_code + view_source + query_dependencies for
    \"how does X work\" / edit-prep questions. Does not modify code.
    """
    result = get_facade().context_explore(
        repo_url=repo_url,
        query=query,
        top_k=top_k,
        blast_depth=blast_depth,
        include_flow=include_flow,
        force_reindex=force_reindex,
    )
    return result.model_dump()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
