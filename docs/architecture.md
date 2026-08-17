# Architecture

RepoScope is three layers: **parse/index**, **store**, **tools**. Agents never see the first two directly.

```
Repository
    → Git checkout + per-file SHA-256
    → tree-sitter AST (definitions) + AST-aligned chunks
    → extract simple names → resolve to symbol_ref
    → JSON artifacts under data/artifacts/<repo_id>/
    → MCP / HTTP  (context_explore, impact, flow, architecture, search)
```

This is not a coding agent. LangGraph (`route → repo_parse → planner → retrieve → analyze → review → finalize`) is an **optional narrative report** path. The default product path is graph + flow + impact over MCP.

---

## Graph schema

A `symbol_ref` is `file/path.py::Class.method` (or `file::function`).

| Artifact | File | Contents |
|---|---|---|
| Chunks | `chunks.json` | AST-bounded snippets with `file:start-end`, `symbol_name`, `content_hash` |
| Dependency graph | `graph.json` | `file_edges`, `call_edges`, `inherit_edges` |
| Definitions | `definitions.json` | Per-file AST defs (name, kind, lines, bases) |
| Knowledge graph | `knowledge_graph.json` | Same facts projected for FlowTracer / ArchitectureAnalyzer |

### Node kinds

`file` · `class` · `function` · `method` (see `app/models/schemas.py`).

### Edge kinds

| Edge | Meaning | Example |
|---|---|---|
| `imports` | File A imports file B | `app/api/auth.py` → `app/services/auth_service.py` |
| `calls` | Caller symbol_ref → callee symbol_ref | `auth.py::login` → `auth_service.py::login` |
| `inherit` | Child → parent (`extends` or `implements`) | `dog.py::Dog` → `base.py::Animal` |

Unresolved names are dropped, not guessed. Duplicate simple names across files: only linked when import map / unique global name / `*Impl` heuristic selects one target.

Storage today is **JSON on disk**, queried in memory. Not SQLite, not Neo4j. That is a deliberate v1 choice (testable, no daemon) and a known scale limit.

---

## Index pipeline

Implemented in `app/ingestion/incremental.py`.

1. **Checkout** — `GitPython` clone/pull a URL, or attach a local path. `repo_id` is a stable hash of the source. `commit_hash` is recorded when `.git` exists.
2. **Enumerate** — source files by extension (`app/parsing/languages.py`). Skip `node_modules`, `.venv`, etc.
3. **Hash** — SHA-256 of file bytes. Compare to last index. Unchanged files reuse chunks + definitions.
4. **Parse** — tree-sitter queries extract function/class/method/interface spans (`app/parsing/ast_parser.py`). Parse failure → line-window fallback chunks, `parse_ok=false`.
5. **Chunk** — align to definition boundaries. v1: a class is one chunk; methods nested in that class are not duplicated (`app/parsing/chunker.py`).
6. **Graph extract** — imports (language-specific), call sites (`name(` / `recv.method(`), class bases.
7. **Graph resolve** — `app/graph/resolver.py`: import map → path stem → unique global class name. Java `field.method()` uses field types; **prefer `TypeImpl`** when present (Spring-style).
8. **Retrieve index** — BM25 pickle + dense vectors (in-memory or Qdrant). Optional. MCP default can run with `HashEmbedder` for offline demos.

---

## Incremental updates

Not a Merkle tree. **Per-file content hash** plus a **graph merge** when the affected set is small.

`graph_update_mode` on every tool `meta`:

| Mode | When |
|---|---|
| `cached` | No changed or deleted files; previous graph reused |
| `merge` | Rebuild outgoing edges only for *affected origins* (changed files ∪ prior callers/importers), then splice into the old graph |
| `full` | First index, `force_reindex`, or origin set too large |

Merge gates (see `app/ingestion/incremental.py`):

- affected origins ≤ 32 files **and**
- affected origins ≤ 35% of the repo

Otherwise full rebuild — merge must not cost more than full.

Why origins include callers: if `a.py` changes, edges *from* `b.py` into `a.py` may be stale. Those outgoing edges are dropped and rebuilt.

Optional file watcher: `python -m app.watch --repo /path` (poll by default; `watchdog` extra).

---

## Retrieval

`app/retrieval/`:

- Channels: dense (cosine) + BM25
- Fusion: RRF (`k=60`) default; weighted min-max is implemented
- Optional cross-encoder rerank
- Optional one-hop graph expand (callers/callees of hit symbols)
- Hit identity: `path:start-end` overlap matching in eval (AST chunks ≠ human spans)

Context assembly (`app/context_engine/`): priority = entry × graph-degree × relevance × tier, then token budget buckets.

---

## Intelligence

| Engine | Input | Output |
|---|---|---|
| FlowTracer | KG + question | Ordered steps with roles (controller/service/repository/…) and `file:line`; synthetic DB/MQ terminals allowed |
| ArchitectureAnalyzer | KG + optional workspace | Modules, coupling, pattern heuristic (`layered` / `mvc` / `hexagonal` / `event_driven` / `unknown`) |
| Impact | `DependencyGraph` + seed refs | BFS: `affected` = callers + subtypes; `depends_on` = callees + parents |

FlowTracer is **not** a runtime tracer. Missed static edges ⇒ truncated paths. Outputs include confidence / warnings.

---

## Tool layer

MCP stdio (`python -m app.mcp.server`) and FastAPI share `RepoScopeFacade`. Every response has `meta`: `repo_id`, `commit_hash`, `indexing_status`, `graph_update_mode`, `warnings`.

If `REPOSCOPE_DATABASE_URL` is unset, audit is in-memory (lost on restart) — surfaced in `meta.warnings`. That is intentional for local demo, not “production persistence”.

See [mcp-tools.md](mcp-tools.md).
