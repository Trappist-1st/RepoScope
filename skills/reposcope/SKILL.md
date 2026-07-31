---
name: reposcope
description: >-
  Explore and understand GitHub/local repositories via RepoScope MCP tools with
  evidence-backed citations (file:line). Use when the user asks about repo
  structure, architecture, entrypoints, call flows, dependencies, or how a
  feature works — and RepoScope MCP tools are available.
---

# RepoScope

RepoScope is repository intelligence infrastructure (not a coding agent).
Prefer its MCP tools over ad-hoc grep when answering structure / flow / dependency questions.
Every claim should cite `file:line` from tool output. Always read `meta.warnings`.

## Tool map

| Tool | Use for |
|---|---|
| `get_initial_context` | First look at an unfamiliar repo |
| `analyze_architecture` | Modules, patterns, coupling, profile |
| `search_code` | Find candidate files/symbols by free text |
| `view_source` | Read a symbol, line range, or file (+ outline) |
| `query_dependencies` | Callers / callees / imports |
| `trace_flow` | Business/code flow (e.g. login) as an evidence path |
| `get_repo_summary` | Narrative architecture summary (heavier; uses workflow) |
| `suggest_refactor` | Refactor ideas for one file with evidence |

## Exploration order

For an unfamiliar repo:

1. `get_initial_context(repo_url)` — README, languages/frameworks, entrypoints, core modules + excerpts
2. `analyze_architecture(repo_url)` and/or `search_code(repo_url, query)` — narrow the area
3. Drill with `view_source` / `query_dependencies` / `trace_flow` as needed

Do **not** start with whole-repo dumps or blind file walks when these tools are available.

## Rules

- Pass a git URL or local path as `repo_url`.
- Prefer `file::symbol` in `query_dependencies` when the symbol name is ambiguous (`notes` will say so).
- `view_source` precedence: `symbol_name` > `(start_line, end_line)` > whole file. Use outline from a file-level call to pick the next symbol.
- Treat architecture patterns as heuristics (`unknown` is valid). Do not invent patterns without evidence.
- Out of scope: editing code, opening PRs, running the repo in a sandbox, multi-turn chat product UX.
- If `meta.warnings` mentions `audit_backend: in_memory`, note that run history is not durable (local demo mode).

## Quick examples

**"What's the overall structure?"**

```
get_initial_context(repo_url)
analyze_architecture(repo_url)   # if more module/coupling detail is needed
```

**"How does login work?"**

```
trace_flow(repo_url, question="How does login work?")
# optional: view_source / query_dependencies on the key steps
```

**"Who calls greet?"**

```
query_dependencies(repo_url, symbol_name="greet", direction="callers")
# if ambiguous: query_dependencies(..., symbol_name="py_pkg/a.py::greet")
```

## Setup (if tools are missing)

RepoScope must be registered as an MCP server in the host (Cursor / Claude Code / Hermes, etc.).
See [docs/mcp_setup.md](../../docs/mcp_setup.md).
