# Architecture

RepoScope is three layers: **parse/index**, **store**, **tools**. Agents never see the first two directly.

```
Repository
    → Git checkout + per-file SHA-256
    → tree-sitter AST (definitions) + AST-aligned chunks
    → extract simple names → resolve to symbol_ref
    → JSON artifacts under data/artifacts/<repo_id>/   (or one reposcope.db)
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
| Structure hashes | `structure_hashes.json` | Per-file AST structure digest; only written when `use_advanced_kg` is on |

With `kg_storage=sqlite`, the knowledge graph and chunks move into `reposcope.db`
(tables `kg_meta` / `kg_nodes` / `kg_edges` / `chunks`); `graph.json`,
`definitions.json`, and `structure_hashes.json` stay as files.

### Node kinds

`file` · `class` · `function` · `method` (see `app/models/schemas.py`).

### Edge kinds

| Edge | Meaning | Example |
|---|---|---|
| `imports` | File A imports file B | `app/api/auth.py` → `app/services/auth_service.py` |
| `calls` | Caller symbol_ref → callee symbol_ref | `auth.py::login` → `auth_service.py::login` |
| `inherit` | Child → parent (`extends` or `implements`) | `dog.py::Dog` → `base.py::Animal` |

Unresolved names are dropped, not guessed. Duplicate simple names across files: only linked when import map / unique global name / `*Impl` heuristic selects one target.

---

## Two optional switches

Both default to off, are independent, and are individually reversible. Turning
both off reproduces the original pipeline byte for byte
(`tests/test_advanced_pipeline.py::test_legacy_mode_is_byte_identical_after_rollback`).

| Setting | Env var | Default | Effect |
|---|---|---|---|
| `use_advanced_kg` | `REPOSCOPE_USE_ADVANCED_KG` | `false` | Cascading call resolution, per-edge confidence and `file:line`, AST structure hashing, leaner `context_explore` payload |
| `kg_storage` | `REPOSCOPE_KG_STORAGE` | `json` | `sqlite` writes graph + chunks into one `reposcope.db` instead of JSON files |
| `kg_min_confidence` | `REPOSCOPE_KG_MIN_CONFIDENCE` | `0.5` | Blast-radius pruning threshold; only consulted when `use_advanced_kg` is on |

The knowledge-graph artifact records which mode produced it
(`source.advanced`). Flipping the switch forces a rebuild rather than mixing
cascade-resolved and legacy edges in one graph.

### Cascading call resolution

With `use_advanced_kg` on, `SymbolResolver.resolve_call` tries six tiers in
descending trust and stops at the first hit, recording the score and the
strategy name on the edge:

| Tier | Strategy | Score |
|---|---|---:|
| Receiver has a known type | `type_resolved` | 0.95 |
| Name was explicitly imported here | `import_map` | 0.95 |
| Defined in this file | `same_module` | 0.90 |
| Present in a module this file imports | `import_suffix` | 0.85 |
| Globally unique simple name | `unique_name` | 0.80 / 0.60 |
| Several candidates, closest by import reachability | `import_distance` | 0.55 |
| Last resort, deliberately conservative | `fuzzy` | ≤ 0.50 |

The ordering matters more than the scores. A *qualified* call (`repo.find()`)
never falls through to same-file lookup: the legacy resolver preferred a
top-level `find` in the calling file over the receiver's actual type, which is
what made same-named symbols such as `find_by_username` bind to the wrong
definition. See `tests/fixtures/name_conflict_repo`.

The advanced path also stops reading declaration headers (`def f(`,
`class C(`) as call sites. On `tests/fixtures/inherit_repo` this removes seven
self-loop edges and keeps the one real cross-file call.

### Storage

Default is **JSON on disk**, queried in memory — a deliberate v1 choice
(testable, no daemon) and a known scale limit.

`kg_storage=sqlite` swaps the artifact files for a single `reposcope.db` with
WAL enabled. It is a storage backend, not a query engine: loaders still
materialise the whole `KnowledgeGraph`, so every in-memory scan keeps working.
Rows carry a content hash, so a re-save only rewrites what changed. Indexes on
`file_path` / `qualified_name` / endpoints exist but are unread today, so a
later query-pushdown pass needs no migration.

Loading falls back across backends in both directions, so switching the
setting does not force a reindex.

Measured honestly, SQLite currently **costs** more than it saves, which is why
it is off by default:

| | JSON | SQLite |
|---|---:|---:|
| Artifact, `flow_fastapi_login` | 11 KB | 72 KB |
| Artifact, `psf/requests` | 1.92 MB | 2.06 MB |
| `context_explore` p50, fixture | 10.5 ms | 79.8 ms |
| `requests` first index | 5083 ms | 4724 ms |
| `requests` comment-only re-index | 110 ms | 116 ms |

The small-repo artifact gap is SQLite's page and index floor, which does not
grow with the repo — by `requests` it is down to 7%. The latency gap is the
one that matters and it is per-call, not per-byte: every tool call reopens the
file and re-materialises the whole graph, and on a 7-file fixture that fixed
cost is the entire measurement. Indexing itself is a wash.

So this is groundwork, not a win yet. It pays off once the graph is large
enough that rewriting the whole artifact on every small edit dominates, and it
is the prerequisite for pushing queries down into SQL instead of scanning in
memory. Until a connection is reused across calls, prefer the JSON default.

---

## Index pipeline

Implemented in `app/ingestion/incremental.py`.

1. **Checkout** — `GitPython` clone/pull a URL, or attach a local path. `repo_id` is a stable hash of the source. `commit_hash` is recorded when `.git` exists.
2. **Enumerate** — source files by extension (`app/parsing/languages.py`). Skip `node_modules`, `.venv`, etc.
3. **Hash** — SHA-256 of file bytes. Compare to last index. Unchanged files reuse chunks + definitions.
4. **Parse** — tree-sitter queries extract function/class/method/interface spans (`app/parsing/ast_parser.py`). Parse failure → line-window fallback chunks, `parse_ok=false`.
5. **Chunk** — align to definition boundaries. v1: a class is one chunk; methods nested in that class are not duplicated (`app/parsing/chunker.py`).
6. **Graph extract** — imports (language-specific), call sites (`name(` / `recv.method(`), class bases.
7. **Graph resolve** — `app/graph/resolver.py`: import map → path stem → unique global class name. Java `field.method()` uses field types; **prefer `TypeImpl`** when present (Spring-style). With `use_advanced_kg`, the six-tier cascade above replaces this and scores every edge.
8. **Retrieve index** — BM25 pickle + dense vectors (in-memory or Qdrant). Optional. MCP default can run with `HashEmbedder` for offline demos.

---

## Incremental updates

Not a Merkle tree. **Per-file content hash** plus a **graph merge** when the affected set is small.

`graph_update_mode` on every tool `meta`:

| Mode | When |
|---|---|
| `cached` | No changed or deleted files; previous graph reused |
| `structure_cached` | File bytes changed but the AST structure hash did not (comments, formatting, line shifts). Chunks and spans refresh; the graph is reused. Requires `use_advanced_kg` |
| `merge` | Rebuild outgoing edges only for *affected origins* (changed files ∪ prior callers/importers), then splice into the old graph |
| `full` | First index, `force_reindex`, or origin set too large |

### AST structure hash

The per-file byte hash is too coarse for the *graph*: adding a comment shifts
every line below it, so the bytes change even though no symbol, base class, or
call site did. `app/ingestion/ast_hash.py` digests only the facts edges are
built from — definition names, kinds, owners, base types, called names, import
targets — and deliberately excludes line numbers. A file whose structure hash
is unchanged is cosmetic and skips graph work entirely.

Files with no recorded previous hash count as structural. Over-rebuilding is
the safe direction.

The hashes live in `structure_hashes.json` next to the other artifacts and are
only written when `use_advanced_kg` is on, so the legacy path keeps producing
exactly the artifact set it always did.

On `psf/requests` a comment-only edit re-indexes in **110 ms** as
`structure_cached`, against **902 ms** for the same edit through `merge`. The
cost is a ~3× slower first index (5.1 s vs 1.7 s), because every call site now
runs the cascade instead of a single lookup. That trade is only good if you
re-index far more often than you index from scratch, which is why the switch
defaults to off.

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

With `use_advanced_kg` on, `context_explore` returns the same facts in fewer
tokens. Two changes, both dedup rather than truncation:

- `report_markdown` is empty. Everything it said is already in `seeds`,
  `must_read`, `call_paths`, and `blast_radius`, and it repeated the snippets.
- A seed that also appears in `must_read` drops its snippet. `must_read` is the
  list the agent is told to read; the seed keeps its ranking and citation.

Blast-radius hits gain a `file:line` evidence span for the call that created
them, which costs tokens back. Net measured on the reproducible harness:
**23.2%** fewer tokens on `flow_fastapi_login`, **24.5%** on `psf/requests`.
Reproduce with `python -m eval.run_benchmarks --advanced-kg --ab`.

If `REPOSCOPE_DATABASE_URL` is unset, audit is in-memory (lost on restart) — surfaced in `meta.warnings`. That is intentional for local demo, not “production persistence”.

See [mcp-tools.md](mcp-tools.md).
