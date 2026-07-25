# RepoScope

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
It is infrastructure that gives models a reliable world-model of a codebase.

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

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

Optional retrieval models / infra:

```powershell
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

trace = FlowTracer().trace(kg, "用户登录流程是什么？")
print(format_flow_markdown(trace))
```

HTTP:

```http
POST /trace
{ "repo_source": "<git-url-or-path>", "question": "用户登录流程是什么？" }
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

```powershell
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

```powershell
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

```powershell
copy .env.example .env
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

```powershell
python -m eval.run_retrieval_eval --compare-modes --hash-embedder
```

---

## Tests

```powershell
pytest -q
```

Includes fixtures for Spring-like and FastAPI-like login flows under `tests/fixtures/`.

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
