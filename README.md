# RepoScope

[![CI](https://github.com/Trappist-1st/RepoScope/actions/workflows/ci.yml/badge.svg)](https://github.com/Trappist-1st/RepoScope/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)

**Structure-aware Code Context Engine** for coding agents — delivered as an **MCP server** (and HTTP API).

RepoScope pre-indexes a repository into a queryable knowledge graph, then returns
**evidence-backed coding context** (`file:line`, call paths, inheritance, blast-radius
signals) so agents like Cursor / Claude Code can stop rediscovering structure via
blind grep/read loops.

```
Repository
    → AST parse + symbol resolution (calls / imports / extends / implements)
    → KnowledgeGraph          (structure)
    → FlowTracer              (behavior)
    → ArchitectureAnalyzer    (design)
    → MCP / HTTP  →  context for agents
```

It is **not** a coding agent, chat product, or auto-PR tool.
It is infrastructure: a world-model of the codebase that agents call as tools.

---

## Positioning

| | RepoScope | Retrieval-only context engines | Graph surgical indexes (e.g. CodeGraph-style) | Full coding agent |
|---|---|---|---|---|
| Hybrid search + chunks | ✅ | ✅ | Partial | Sometimes |
| Call / import / inherit graph | ✅ | Weak / expand-only | ✅ (core) | Opaque |
| Business flow trace (`file:line`) | ✅ | ❌ | Partial | Sometimes |
| Architecture (modules / coupling / patterns) | ✅ | ❌ | Rarely | Rarely |
| Makes edits / opens PRs | ❌ (by design) | ❌ | ❌ | ✅ |

**Subtype we commit to:** structure-aware context engine (graph + flow + architecture),
not a pure “save tokens by chunk search” product and not an autonomous coder.

---

## What it does

| Capability | Question it answers | Output |
|---|---|---|
| **KnowledgeGraph** | How is the code structured? | Files / classes / functions / methods + import / call / **inherit** edges |
| **FlowTracer** | How does a flow run? | Evidence-backed call path with file:line + roles |
| **ArchitectureAnalyzer** | How is the system organized? | Modules, patterns, profile, coupling findings |

Supporting stack:

- Hybrid RAG (dense + BM25 + RRF + optional rerank / Qdrant)
- LangGraph research workflow (`summary` / `interview` / `refactor`) with citation review
- FastAPI + MCP tool surface for agents

**Language coverage today:** Python, JavaScript, TypeScript, Java (via
tree-sitter). Go / Rust / C-family are natural next targets — see
[CONTRIBUTING.md](CONTRIBUTING.md).

---

## Product boundary

**In scope**

- Structured repository indexing and symbol resolution
- Evidence-backed context for agents (paths, symbols, lines)
- Library + MCP + HTTP APIs

**Out of scope (by design)**

- Coding Agent / automatic code edits
- Chat UI / multi-turn product UX
- Infinite agent orchestration for its own sake

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest -q
```

Optional retrieval models / infra:

```bash
pip install -e ".[dev,retrieval]"
docker compose up -d
# REPOSCOPE_VECTOR_BACKEND=qdrant
# REPOSCOPE_DATABASE_URL=postgresql://reposcope:reposcope@localhost:5432/reposcope
```

---

## Core APIs

### KnowledgeGraph (ingest artifact)

```python
from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion import IngestionPipeline

pipe = IngestionPipeline(
    files_repo=InMemoryFilesRepository(),
    repos_repo=InMemoryReposRepository(),
)
result = pipe.run("tests/fixtures/sample_repo")
kg = pipe.load_knowledge_graph(result.repo_id)
print(kg.stats.node_counts, kg.stats.edge_counts)
```

Artifacts per repo: `chunks.json`, `graph.json`, `definitions.json`, `knowledge_graph.json`.

Graph edges today: **import**, **call**, **inherit** (`extends` / `implements`),
resolved across files via import maps + path index (Spring `*Impl` preference for calls).

### Flow Trace

```python
from app.intelligence.flow_tracer import FlowTracer
from app.intelligence.flow_format import format_flow_markdown

trace = FlowTracer().trace(kg, "How does login work?")
print(format_flow_markdown(trace))
```

```http
POST /trace
{ "repo_source": "<git-url-or-path>", "question": "How does login work?" }
```

### Architecture Intelligence

```python
from app.intelligence.architecture import analyze_architecture_markdown

report, md = analyze_architecture_markdown(kg, workspace_root="path/to/repo")
print(report.primary_pattern, len(report.findings))
```

```http
POST /architecture
{ "repo_source": "<git-url-or-path>" }
```

---

## MCP tools

```bash
python -m app.mcp.server
```

| Tool | Purpose |
|---|---|
| **`context_explore`** | **Primary:** one-shot surgical context (seeds + must-read + call paths + blast radius) |
| **`analyze_impact`** | Dedicated N-hop blast radius: who is affected / what it depends on |
| `get_initial_context` | Repository launchpad: README + profile + core modules + core file source |
| `get_repo_summary` | Citation-backed summary (LangGraph workflow) |
| `search_code` | Hybrid BM25 + vector search over indexed code chunks |
| `view_source` | Read a symbol, a line range, or a whole file (+ outline) |
| `query_dependencies` | Callers / callees / imports (1-hop) |
| `suggest_refactor` | Refactor suggestions with evidence |
| `trace_flow` | Flow Trace (call path understanding) |
| `analyze_architecture` | Modules / patterns / coupling / profile |

Each tool auto-syncs the index on call (`meta.indexing_status`, `meta.graph_update_mode`:
`full` / `merge` / `cached`). Small edits prefer **merge** graph updates.

Optional **file watcher** keeps artifacts fresh between tool calls (poll by default;
`watchdog` if installed):

```bash
python -m app.watch --repo /path/to/project
# REPOSCOPE_WATCH_DEBOUNCE_MS=2000  REPOSCOPE_WATCH_POLL_MS=1500
```

Setup: [`docs/mcp_setup.md`](docs/mcp_setup.md)

Also:

```bash
uvicorn app.api.main:app --reload
# GET  /health
# POST /analyze/stream
# POST /trace
# POST /architecture
```

Without `REPOSCOPE_DATABASE_URL`, audit is in-memory and lost on restart — surfaced in `meta.warnings`.

---

## Research workflow (optional)

LangGraph path for report-style analysis:

`route → repo_parse → planner → retrieve → analyze → review → finalize`

Prefer **FlowTracer / ArchitectureAnalyzer / KnowledgeGraph queries** when you need
structured context for agents; use the workflow when you want a narrative report
with review loops.

---

## Retrieval & evaluation

- Hybrid RAG: dense + BM25, RRF default, optional cross-encoder rerank
- Graph / chunks live as JSON under `data/artifacts/<repo_id>/` (not SQLite)
- Keyword search is BM25 (pickle), not SQLite FTS5 — FTS5 fits CodeGraph-style
  SQLite stores; migrating storage would be the reason to add it, not search alone
- Config: `config/retrieval.yaml`
- Eval harness: [`eval/README.md`](eval/README.md)

```bash
python -m eval.run_retrieval_eval --compare-modes --hash-embedder
```

---

## Tests

```bash
pytest -q
```

Fixture-driven coverage includes Spring-like and FastAPI-like login flows, plus
inherit/resolution regression fixtures under `tests/fixtures/`.

---

## Mental model

```
                 RepoScope
                     |
      Structure-aware Code Context Engine (MCP)
            /              |              \
   KnowledgeGraph     FlowTracer    ArchitectureAnalyzer
     structure         behavior            design
   import/call/inherit
```

Extend only when a real agent need appears — not by drifting into chat or auto-coding.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — language grammars, symbol resolution
fixtures, and flow-tracing cases for real frameworks are the highest-leverage
contributions right now.

## License

[MIT](LICENSE)
