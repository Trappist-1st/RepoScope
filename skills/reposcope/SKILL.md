---
name: reposcope
description: >-
  Explore and understand GitHub/local repositories via RepoScope MCP tools with
  evidence-backed citations (file:line). Use when the user asks about repo
  structure, architecture, entrypoints, call flows, dependencies, or how a
  feature works — and RepoScope MCP tools are available.
---

# RepoScope

RepoScope is a **structure-aware code context engine** (MCP) — not a coding agent.
Prefer its tools over ad-hoc grep when answering structure / flow / dependency questions.
Every claim should cite `file:line` from tool output. Always read `meta.warnings`.

## Tool map

| Tool | Use for |
|---|---|
| **`context_explore`** | **Default:** one call → seeds + must-read source + call paths + blast radius |
| **`analyze_impact`** | Before editing: N-hop who is affected / what depends on the symbol |
| `get_initial_context` | First look at an unfamiliar repo (launchpad) |
| `analyze_architecture` | Modules, patterns, coupling, profile |
| `search_code` | Find candidate files/symbols by free text |
| `view_source` | Read a symbol, line range, or file (+ outline) |
| `query_dependencies` | 1-hop callers / callees / imports |
| `trace_flow` | Business/code flow as an evidence path |
| `get_repo_summary` | Narrative architecture summary (heavier; uses workflow) |
| `suggest_refactor` | Refactor ideas for one file with evidence |

## Exploration order

For an unfamiliar repo:

1. `get_initial_context(repo_url)` — map the territory
2. `context_explore(repo_url, query=...)` — get surgical context for the task
3. Drill with `view_source` / `query_dependencies` / `analyze_impact` / `trace_flow` only if needed

Do **not** start with whole-repo dumps or blind file walks when these tools are available.

## Rules

- Pass a git URL or local path as `repo_url`.
- Prefer `context_explore` for "how does X work" / edit-prep questions.
- Prefer `analyze_impact` when the question is specifically blast radius / change impact (`depth`, `direction=affected|depends_on|both`).
- Prefer `file::symbol` in `query_dependencies` / `analyze_impact` when the symbol name is ambiguous (`notes` will say so).
- `view_source` precedence: `symbol_name` > `(start_line, end_line)` > whole file.
- Treat architecture patterns as heuristics (`unknown` is valid).
- Out of scope: editing code, opening PRs, running the repo in a sandbox, multi-turn chat product UX.
- If `meta.warnings` mentions `audit_backend: in_memory`, note that run history is not durable.
- `meta.graph_update_mode` tells you how the index synced: `cached` / `merge` / `full`.

## Quick examples

**"How does login work?" / prep to edit auth**

```
context_explore(repo_url, query="How does login work?")
```

**"What's the overall structure?"**

```
get_initial_context(repo_url)
analyze_architecture(repo_url)   # if more module/coupling detail is needed
```

**"Who calls greet?"**

```
query_dependencies(repo_url, symbol_name="greet", direction="callers")
# if ambiguous: query_dependencies(..., symbol_name="py_pkg/a.py::greet")
```

**"What breaks if I change Animal / greet?"**

```
analyze_impact(repo_url, symbol_name="animal/base.py::Animal", depth=2, direction="affected")
analyze_impact(repo_url, symbol_name="py_pkg/a.py::greet", depth=2, direction="both")
```

## Setup (if tools are missing)

RepoScope must be registered as an MCP server in the host (Cursor / Claude Code / Hermes, etc.).
See [docs/mcp_setup.md](../../docs/mcp_setup.md).
