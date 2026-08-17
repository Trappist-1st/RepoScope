# RepoScope

[![CI](https://github.com/Trappist-1st/RepoScope/actions/workflows/ci.yml/badge.svg)](https://github.com/Trappist-1st/RepoScope/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**One MCP call that returns the call path, blast radius, and `file:line` evidence for the next edit — not another grep loop.**

RepoScope is a **structure-aware context engine for coding agents**. It pre-indexes a repository into a knowledge graph (import / call / inherit), then answers “how does this flow run?” and “what breaks if I change this?” with citations. It does **not** write code, open PRs, or chat.

```
Without RepoScope          With RepoScope
─────────────────          ────────────────────────────
grep login                 context_explore("how does login work?")
read 8 files               → seeds + must-read snippets
guess the chain            → call path with file:line
miss a subtype             → blast radius (callers + subtypes)
```

Numbers, methodology, and caveats: **[BENCHMARKS.md](BENCHMARKS.md)**. Re-run with `python -m eval.run_benchmarks`.

---

## Why this exists

Most MCP code-graph tools sell the same story: *index the repo, save tokens, help the agent*. The crowded part is “who calls this function?”. The gap we actually hit in Cursor / Claude Code is different:

| Task an agent has before editing | Typical MCP graph | RepoScope |
|---|---|---|
| Find a symbol | search / FTS | hybrid BM25 + vector |
| Who calls `login`? | callers | callers + inherit |
| **How does login run, layer by layer?** | rarely | **FlowTracer** (`file:line` + roles) |
| **What is affected if I change it?** | sometimes | **impact BFS** (callers ∪ subtypes) |
| **How is the repo organized?** | rarely | **ArchitectureAnalyzer** (modules / coupling / patterns) |
| Can I trust the answer? | opaque | every claim cites `file:line`; workflow Reviewer rejects fabricated paths |

---

## Honest comparison

Feature claims below are from each project’s public README / BENCHMARKS as of 2026-08, not a head-to-head we ran. We do **not** claim to beat them on index speed, language count, or kernel-scale ingest. See [BENCHMARKS.md](BENCHMARKS.md) for what we *did* measure.

| | RepoScope | [codescope-mcp](https://github.com/abdulmunimjemal/codescope-mcp) | [code-graph-mcp](https://github.com/sdsrs/code-graph) | [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) |
|---|---|---|---|---|
| Primary bet | Flow + blast radius + architecture, cited | Lean, fast, accurate **callers** | Call graph + semantic search + HTTP routes | Native binary, huge language coverage, Cypher |
| Flow / business path with roles | **Yes** | No (callers/callees/impact) | Partial (route tracing) | Call-path BFS |
| Architecture (modules / coupling / patterns) | **Yes** (heuristic) | No | Limited | Community detection / wiki |
| Hybrid BM25 + dense retrieval | **Yes** | FTS-oriented | Semantic search | Structural queries |
| Citation / anti-hallucination review | **Yes** (rule-based) | Token-compact answers | — | — |
| Languages today | Python, JS, TS, Java | ~21 | ~16 | 100+ / kernel-scale demos |
| Install | Python 3.11+ package | `npx` / CLI | npm / Rust | Single native binary |
| Index speed / kernel-scale | **Behind** (CPython + JSON artifacts) | Strong published numbers | Strong | **Linux kernel in minutes** (their claim) |
| Zero-install MCP | **Not yet** | Yes | Yes | Yes |

**Pick RepoScope** when the agent’s question is “how does this feature run / what does this edit hit / how is this repo shaped?”, and you want the answer checkable.

**Pick the others** when you need 20+ languages, millisecond native queries, or a drop-in `npx` binary. We are not competing on those axes yet.

---

## 30-second quickstart

Python 3.11+, no Docker required for the MCP path. There is **no** `npx reposcope` wrapper yet — that is a real gap vs codescope-mcp.

```bash
git clone https://github.com/Trappist-1st/RepoScope.git
cd RepoScope
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m app.mcp.server           # stdio MCP
```

Cursor / Claude Code / Windsurf: paste the block in [Connect](#connect-to-agents). Then:

```
context_explore(
  repo_url="<git-url-or-local-path>",
  query="How does login work?"
)
```

Optional tests: `pytest -q`. Optional real embeddings: `pip install -e ".[retrieval]"`.

---

## Task-level examples

Gold paths below are the FastAPI login fixture (`tests/fixtures/flow_fastapi_login`). Token counts are the **proxy** in [BENCHMARKS.md](BENCHMARKS.md) (chars/4, not a live SWE-bench).

### 1. Understand a flow before touching it

**Question:** “How does login work?”

| | Blind agent | RepoScope |
|---|---|---|
| Tools | grep + N file reads | **1×** `context_explore` or `trace_flow` |
| Output | fragments | `app/api/auth.py::login` → `auth_service.login` → `find_by_username` with `file:line` |

```text
trace_flow(repo_url=<path>, question="How does login work?")
```

### 2. Blast radius before a refactor

**Question:** “If I change `Animal.speak`, who is affected?”

```text
analyze_impact(repo_url=<path>, symbol_name="animal/base.py::Animal", direction="affected", depth=2)
```

Expect subtypes (`Dog`) and callers, not a keyword dump of the word “Animal”.

### 3. Agent prep for a PR

**Question:** “I’m about to edit `AuthService.login` — what must I read, and what might break?”

```text
context_explore(repo_url=<path>, query="AuthService.login change impact")
```

Returns seeds, must-read snippets, call paths, and blast radius. Out of scope: opening the PR (by design).

### 4. First look at an unfamiliar repo

```text
get_initial_context(repo_url=<git-url-or-path>)
analyze_architecture(repo_url=<same>)   # modules / coupling / pattern heuristic
```

### 5. Find a symbol, then drill in

```text
search_code(repo_url=<path>, query="find_by_username")
view_source(repo_url=<path>, file_path="app/repositories/user_repo.py", symbol_name="find_by_username")
query_dependencies(repo_url=<path>, symbol_name="file::symbol", direction="callers")
```

---

## Core capabilities

| Capability | Question | Minimal call |
|---|---|---|
| **KnowledgeGraph** | How is it wired? | `query_dependencies(..., direction="both")` |
| **FlowTracer** | How does a feature run? | `trace_flow(..., question="How does login work?")` |
| **Impact** | What does this edit hit? | `analyze_impact(..., direction="affected")` |
| **Architecture** | How is it organized? | `analyze_architecture(...)` |
| **Surgical pack** | All of the above for one task | **`context_explore`** (default tool) |

Graph edges today: **import**, **call**, **inherit** (`extends` / `implements`). Languages: Python, JavaScript, TypeScript, Java (tree-sitter).

---

## Connect to agents

Stdio server: `python -m app.mcp.server`. Use an **absolute** interpreter path.

### Cursor

`.cursor/mcp.json` (see [docs/mcp_setup.md](docs/mcp_setup.md)):

```json
{
  "mcpServers": {
    "reposcope": {
      "command": "/ABS/PATH/RepoScope/.venv/bin/python",
      "args": ["-m", "app.mcp.server"],
      "env": {
        "PYTHONPATH": "/ABS/PATH/RepoScope",
        "REPOSCOPE_ARTIFACT_DIR": "/ABS/PATH/RepoScope/data/artifacts",
        "REPOSCOPE_WORKSPACE_ROOT": "/ABS/PATH/RepoScope/data/workspace"
      }
    }
  }
}
```

Windows: `.venv\\Scripts\\python.exe`.

### Claude Code / Claude Desktop

Same `mcpServers.reposcope` object in Claude’s MCP config. Prefer `context_explore` over chaining grep.

### Codex / Windsurf / other MCP clients

Any client that speaks MCP stdio can point `command` at the venv Python and `args` at `-m app.mcp.server`.

Tool schemas: [docs/mcp-tools.md](docs/mcp-tools.md).

---

## Architecture (one picture)

```
Repository
    → Git checkout + file SHA-256
    → tree-sitter AST (defs / chunks)
    → resolve imports, calls, inherit     ← parse / index
    → JSON artifacts (graph + chunks)     ← store
    → MCP / HTTP tools                    ← context_explore, impact, flow, …
```

Details: [docs/architecture.md](docs/architecture.md).

**In scope:** indexing, symbol resolution, evidence-backed context.  
**Out of scope:** coding agent, chat UI, automatic edits / PRs.

---

## Benchmarks (reproducible)

Snapshot (2026-08-15, MiniLM, `--real-embed`): **18/18** gold graph edges; FlowTracer **2/2** login fixtures; Reviewer **4/4** fabricated citations caught; `psf/requests` full index **3.7 s** / **14.5k LOC**; warm `context_explore` **p50 49 ms**. On `requests`, one `context_explore` vs grep+full-file reads was **17× fewer tokens / 47→1 tool calls** (chars/4 proxy). Hybrid RRF did **not** beat dense-only Recall@5 on this 18-question mix — details and everything we refuse to claim: **[BENCHMARKS.md](BENCHMARKS.md)**.

| Axis | What we publish | What we do **not** publish yet |
|---|---|---|
| Structure quality | Gold-edge recall on fixtures; flow term coverage; reviewer catch rate | LSP/compiler caller F1 vs codescope; historical-PR impact GT |
| Retrieval | Recall@5 / Precision@5, vector vs BM25 vs hybrid | “Hybrid always wins”; rerank as default official number |
| Performance | Ingest ms, incremental vs full, query p50/p95 | Linux-kernel index; native-binary comparison |
| Token proxy | `context_explore` vs grep+full-file reads (chars/4) | Live-agent SWE-bench success rate |

**Do not quote HashEmbedder smoke runs as Hybrid lift.** Official retrieval: MiniLM (or Qdrant) via `--real-embed`.

```bash
python -m eval.run_benchmarks --real-embed
# skip clone:  --skip-remote
```

Raw output: `eval/reports/latest.md` (gitignored). Narrative + tables: [BENCHMARKS.md](BENCHMARKS.md).

### Performance (measured, not marketing)

Indexed files = supported languages, tests/venv excluded. Full methodology: [eval/README.md](eval/README.md).

| Corpus | Indexed files | LOC | First index | Artifact | Notes |
|---|---:|---:|---:|---:|---|
| `psf/requests` (shallow) | 50 | 14 556 | **3.7 s** | 1.79 MB | 867 call / 41 inherit edges |
| FastAPI login fixture | 7 | 30 | 18 ms | 10 KB | Warm `context_explore` p50 **49 ms** |

There is **no** “5000-file Spring Boot, X seconds” row yet. To produce one:

```bash
python -m eval.run_perf --bucket medium   # flask / sqlalchemy
python -m eval.run_perf --bucket large    # django; slow; do not guess the number
```

Quote only `eval/reports/perf_*.json` after that run.

---

## HTTP API (optional)

```bash
pip install -e ".[api]"
uvicorn app.api.main:app --reload
# GET /health   POST /trace   POST /architecture   POST /analyze/stream
```

---

## Docs

| Doc | Contents |
|---|---|
| [BENCHMARKS.md](BENCHMARKS.md) | Methodology, numbers, what is *not* claimed |
| [docs/architecture.md](docs/architecture.md) | Schema, pipeline, incremental merge |
| [docs/mcp-tools.md](docs/mcp-tools.md) | Every MCP tool: I/O, when to use it |
| [docs/mcp_setup.md](docs/mcp_setup.md) | Client config, Inspector, audit warnings |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, tests, **adding a language** |

## License

[MIT](LICENSE)
