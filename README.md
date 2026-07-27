# RepoScope

[![CI](https://github.com/Trappist-1st/RepoScope/actions/workflows/ci.yml/badge.svg)](https://github.com/Trappist-1st/RepoScope/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)

**Repository Intelligence Engine** for LLMs and agents.

RepoScope turns a GitHub / local repository into structured, evidence-backed understanding:

```
Repository
    → AST + incremental index
    → KnowledgeGraph          (structure)
    → FlowTracer              (behavior)
    → ArchitectureAnalyzer    (design)
    → MCP / HTTP API
```

It is **not** a coding agent, chat product, or auto-PR tool.
It is infrastructure that gives models a reliable world-model of a codebase —
plug it into Claude Code, Cursor, or your own agent loop as an MCP server or
HTTP API, instead of re-grepping the repo on every turn.

---

## Why RepoScope (vs. grep, embeddings-only RAG, or a full coding agent)

| | RepoScope | Plain semantic RAG | ctags / grep | Full coding agent |
|---|---|---|---|---|
| Answers are evidence-backed (`file:line`) | ✅ | Partial (chunk-level) | ✅ | Depends on the agent |
| Understands call graphs, not just text similarity | ✅ | ❌ | ❌ | Sometimes, opaquely |
| Structured output (JSON/graph), not prose | ✅ | ❌ | ❌ | ❌ |
| Architecture-level findings (patterns, coupling) | ✅ | ❌ | ❌ | Rarely |
| Makes edits / opens PRs | ❌ (by design) | ❌ | ❌ | ✅ |
| Multi-turn chat UI | ❌ (by design) | Depends | ❌ | ✅ |

RepoScope's bet: agents don't need another chat window, they need a
**queryable, cached, evidence-backed model of the repo** they can call as a
tool. The three engines below are that model.

---

## What it does

| Capability | Question it answers | Output |
|---|---|---|
| **KnowledgeGraph** | How is the code structured? | Files / classes / functions / methods + import/call edges |
| **FlowTracer** | How does a flow run? | Evidence-backed call path with file:line + roles |
| **ArchitectureAnalyzer** | How is the system organized? | Modules, patterns, profile, coupling findings |

Supporting stack (still available):

- Hybrid RAG (dense + BM25 + optional rerank / Qdrant)
- LangGraph research workflow (`summary` / `interview` / `refactor`) with citation review
- FastAPI + MCP tool surface for agents

**Language coverage today:** Python, JavaScript, TypeScript, Java (via
tree-sitter). Go / Rust / C-family are natural next targets — see
[CONTRIBUTING.md](CONTRIBUTING.md) if you want to add one.

---

## Product boundary

**In scope**

- Structured repository understanding
- Evidence-backed findings (paths, symbols, lines)
- Library + MCP + HTTP APIs for agents / tooling

**Out of scope (by design)**

- Coding Agent / automatic code edits
- Chat UI / multi-turn explore sessions
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
from app.intelligence import load_knowledge_graph

pipe = IngestionPipeline(
    files_repo=InMemoryFilesRepository(),
    repos_repo=InMemoryReposRepository(),
)
result = pipe.run("tests/fixtures/sample_repo")
kg = pipe.load_knowledge_graph(result.repo_id)
print(kg.stats.node_counts, kg.stats.edge_counts)
```

Artifacts per repo: `chunks.json`, `graph.json`, `definitions.json`, `knowledge_graph.json`.

### Flow Trace

```python
from app.intelligence.flow_tracer import FlowTracer
from app.intelligence.flow_format import format_flow_markdown

trace = FlowTracer().trace(kg, "How does login work?")
print(format_flow_markdown(trace))
```

Example output (shape produced by `format_flow_markdown`, run against a
Spring-style login flow fixture):

```markdown
## Flow Trace: login flow

**Question:** How does login work?
**Confidence:** high
**Score:** 0.92

### Steps
1. **AuthController.login** (`entrypoint`, high) — `src/main/java/AuthController.java:24-31`
2. **AuthService.authenticate** (`service`, high) — `src/main/java/AuthService.java:18-40`
3. **UserRepository.findByUsername** (`repository`, high) — `src/main/java/UserRepository.java:12-15`

### Alternatives
1. `AuthController.login → SessionManager.create` (score=0.41, medium)
```

Every step carries a `file:line` and a role (`entrypoint` / `service` /
`repository` / …) plus a confidence — nothing in the output is unattributed.

HTTP:

```http
POST /trace
{ "repo_source": "<git-url-or-path>", "question": "How does login work?" }
```

### Architecture Intelligence

```python
from app.intelligence.architecture import analyze_architecture_markdown

report, md = analyze_architecture_markdown(kg, workspace_root="path/to/repo")
print(report.primary_pattern, len(report.findings))
print(md)
```

HTTP:

```http
POST /architecture
{ "repo_source": "<git-url-or-path>" }
```

Modules are path clusters with honest typing (`feature` / `layer` / `technical` / `unknown`) and `boundary_confidence` — not every folder is treated as a domain module.

---

## MCP tools

```bash
python -m app.mcp.server
```

| Tool | Purpose |
|---|---|
| `get_repo_summary` | Citation-backed summary (LangGraph workflow) |
| `query_dependencies` | Callers / callees / imports |
| `suggest_refactor` | Refactor suggestions with evidence |
| `trace_flow` | Flow Trace (call path understanding) |
| `analyze_architecture` | Modules / patterns / coupling / profile |

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

```bash
cp .env.example .env
# REPOSCOPE_LLM_API_KEY=...
```

```python
from app.workflow import WorkflowInput, create_default_runner

runner = create_default_runner(use_hash_embedder=True)
result = runner.run(WorkflowInput(
    question="Summarize the architecture",
    repo_source="tests/fixtures/sample_repo",
    intent_hint="summary",
))
print(result.report_markdown)
```

Prefer **FlowTracer / ArchitectureAnalyzer** when you need structured intelligence for agents; use the workflow when you want a narrative report with review loops.

---

## Retrieval & evaluation

- Hybrid RAG: dense + BM25, RRF default, optional cross-encoder rerank
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

104 tests, fixture-driven — includes fixtures for Spring-like and FastAPI-like login flows under `tests/fixtures/`.

---

## Mental model

```
            RepoScope
                |
     Repository Intelligence Engine
       /            |             \
KnowledgeGraph  FlowTracer  ArchitectureAnalyzer
   structure      behavior         design
```

That is the intended core. Extend only when a real agent/product need appears — not by drifting into chat or auto-coding.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — language grammars and flow-tracing
fixtures for real-world frameworks are the highest-leverage contributions
right now.

## License

[MIT](LICENSE)
