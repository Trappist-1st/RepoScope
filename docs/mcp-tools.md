# MCP tools

Server: `python -m app.mcp.server` (stdio). Shared logic: `app/mcp/service.py`. Pydantic shapes: `app/mcp/schemas.py`.

**Default tool for “how does X work / what should I read before editing”:** `context_explore`.

Always read `meta.warnings` and `meta.graph_update_mode` (`full` | `merge` | `cached` | `structure_cached`). Prefer `file::symbol` when a short name is ambiguous.

`structure_cached` means the file bytes changed but the AST structure did not (comments, formatting), so the previous graph was reused. It only appears when `use_advanced_kg` is on.

All tools take `repo_url` (git URL or local path) and `force_reindex: bool = false`. None of them modify the repository.

---

## `context_explore`

One call: seed symbols + must-read snippets + call paths + blast radius.

| Arg | Type | Default | Notes |
|---|---|---|---|
| `query` | string | required | Natural language |
| `top_k` | int | 8 | Seed cap |
| `blast_depth` | int | 2 | Impact BFS depth |
| `include_flow` | bool \| null | null | If null, run FlowTracer when the query looks like a flow |

**When:** how-it-works, edit prep. **When not:** you already know the symbol and only need callers (`query_dependencies` / `analyze_impact`).

```json
{ "repo_url": "/path/to/repo", "query": "How does login work?" }
```

With `use_advanced_kg` on, the same facts come back in about 23% fewer tokens:
`report_markdown` is empty (everything it said is in the structured fields), and
a seed that also appears in `must_read` drops its duplicated snippet while
keeping its rank and citation. Blast-radius hits gain an `evidence` span
pointing at the line the call was observed on. Read `must_read` for source,
`seeds` for ranking.

---

## `analyze_impact`

N-hop blast radius on the dependency graph.

| Arg | Type | Default |
|---|---|---|
| `symbol_name` | string | required; prefer `file::symbol` |
| `depth` | int | 2 (clamped 1–8) |
| `direction` | `affected` \| `depends_on` \| `both` | `both` |
| `limit` | int | 50 (clamped ≤ 200) |

- `affected`: callers + subtypes (what feels the change)
- `depends_on`: callees + super-types (what you must understand)

```json
{
  "repo_url": "/path/to/repo",
  "symbol_name": "animal/base.py::Animal",
  "direction": "affected",
  "depth": 2
}
```

---

## `trace_flow`

Business/code flow as an evidence path (FlowTracer).

| Arg | Type | Default |
|---|---|---|
| `question` | string | required |
| `entry_hint` | string \| null | null |
| `max_depth` | int | 5 |

Returns steps with `file:line`, role, and optional synthetic DB/MQ terminal. Does not execute the program.

```json
{ "repo_url": "/path/to/repo", "question": "用户登录流程是什么？" }
```

---

## `query_dependencies`

One-hop neighbors.

| Arg | Type | Default |
|---|---|---|
| `symbol_name` | string | required |
| `direction` | `both` \| `callers` \| `callees` \| `imports` | `both` |
| `limit` | int | 20 |

If `query.resolved_refs` has multiple entries, pass `file::symbol`.

---

## `get_initial_context`

Launchpad for an unfamiliar repo: README excerpt, languages/frameworks, entrypoints, top-coupling modules with a source excerpt, remaining module paths. No LLM.

| Arg | Type | Default |
|---|---|---|
| `top_k_modules` | int | 8 |
| `top_k_core_files` | int | 5 |

Call this **first** on a new repository.

---

## `analyze_architecture`

Heuristic architecture report: modules, coupling, pattern (`layered` / `mvc` / `hexagonal` / `event_driven` / `unknown`). Evidence-backed, not a chat essay. `unknown` is a valid result.

---

## `search_code`

Hybrid BM25 + vector over chunks.

| Arg | Type | Default |
|---|---|---|
| `query` | string | required |
| `top_k` | int | 10 |
| `graph_expand` | bool | false |

Hits always include `file:line`. Weak ranking may fall back to a diversity sample (`notes` explains this).

---

## `view_source`

Read by **symbol**, then line range, then file (capped ~400 lines + outline).

| Arg | Type | Default |
|---|---|---|
| `file_path` | string | required |
| `symbol_name` | string \| null | null |
| `start_line` / `end_line` | int \| null | null |

If truncated: `next_start_line` / `total_lines`.

---

## `get_repo_summary`

Citation-backed narrative via LangGraph (heavier). Prefer graph tools unless you need a written summary. Check `review_passed` / `low_confidence`.

---

## `suggest_refactor`

File-scoped suggestions with evidence. Expanded-only evidence is capped at `confidence=medium`.

---

## Suggested order

1. Unfamiliar repo → `get_initial_context`
2. Task context → `context_explore`
3. Drill → `view_source` / `query_dependencies` / `analyze_impact` / `trace_flow`

Client install: [mcp_setup.md](mcp_setup.md).
