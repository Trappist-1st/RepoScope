# RepoScope benchmarks

This file is the **public, quoted** record. Raw machine output is `eval/reports/latest.md` (gitignored). Re-run:

```bash
python -m eval.run_benchmarks --real-embed
python -m eval.run_benchmarks --skip-remote          # fixtures only
python -m eval.run_benchmarks --real-embed --repo /path/to/local/repo
```

Gold set: [`eval/gold/structure.json`](eval/gold/structure.json)  
QA set: [`eval/datasets/qa_dataset.jsonl`](eval/datasets/qa_dataset.jsonl)  
Harness: [`eval/run_benchmarks.py`](eval/run_benchmarks.py)

We publish **method + numbers + limits**. We do not publish “120× token savings” or kernel-scale claims we have not run.

---

## How to build / extend this benchmark

Three layers, in order. Details: [`eval/README.md`](eval/README.md).

1. **Retrieval (core)** — annotate `eval/datasets/qa_dataset.jsonl` on 3–5 *classic* public repos (`psf/requests` is in; `httpx` / `flask` / `sqlalchemy` / `django` are listed in `eval/repos.yaml` but unlabeled). Score Precision@K, Recall@K, **MRR@K**. Compare `vector` / `bm25` / `hybrid` / optional `hybrid+rerank`. Matching = file path + overlapping lines (AST chunks ≠ human spans).
2. **NL → tools (Spider/BIRD-style)** — annotate `eval/gold/tools.jsonl`: a natural-language question plus the expected `query_dependencies` callers/callees or `trace_flow` ordered chain. `gold_complete: true` is required before we quote precision.
3. **MCP tasks** — annotate `eval/gold/mcp_tasks.jsonl` with an agent goal (“找出修改用户认证逻辑的影响范围”). A **scripted** policy (`explore_first` / `flow_first` / `impact_first`) calls MCP tools; success = must-files/symbols appear in the JSON. This is not a live LLM agent and not SWE-bench.
4. **Perf buckets** — `python -m eval.run_perf --bucket small|medium|large`. Report *measured* indexed file counts (tests excluded). Do not copy YAML bucket labels if django is the first repo that actually exceeds 5000 files.

RAGAS/ARES-style *LLM* faithfulness judges are out of scope until we have a frozen judge prompt and a labeled set. The current faithfulness proxy is “every FlowTracer step has `file:line`”.

Historical-PR impact ground truth (actual files touched by a signature-change PR vs `analyze_impact`) is still the next gold-standard upgrade — not in this snapshot.

---

## What this is / is not

| We measure | We do **not** measure (yet) |
|---|---|
| Fixture **gold-edge recall** for a curated must-resolve set (import / call / inherit) | Full caller F1 vs TypeScript compiler / LSP (codescope-mcp’s method) |
| FlowTracer **term coverage** on annotated login fixtures | Runtime traces, dynamic dispatch |
| Reviewer **catch rate** on synthetic fabricated citations | LLM-as-judge of architecture prose |
| Retrieval Recall@5 / Precision@5 (file + overlapping lines) | Cross-encoder rerank as the default official figure |
| Ingest wall time, incremental vs full, artifact bytes | Linux kernel; 10k-file native-binary bake-off |
| Query **p50 / p95 / p99** on a warm fixture | Cold-start of a 100k-file graph |
| **Token proxy**: one `context_explore` vs grep + full-file reads | Live agent SWE-bench pass@k |

**Gold-edge recall is not precision.** The gold set is “edges we require”, not “all true edges in the program”. Reporting precision against an incomplete gold would look better than it is; we refuse that.

**Token proxy is not an agent eval.** Estimate = `ceil(chars/4)`. “grep+read” = content scan + read every matching source file whole. A real agent may grep worse or better. Treat the ratio as an order-of-magnitude illustration, not a product SLA.

**HashEmbedder retrieval is CI/smoke.** Official retrieval rows must say `sentence-transformers/all-MiniLM-L6-v2` (or Qdrant) and `rerank=off|on`.

---

## Dataset selection

| Corpus | Why it is in | Why it is not “the whole industry” |
|---|---|---|
| `tests/fixtures/sample_repo` | Multi-language smoke (py/js/ts/java) | Toy size |
| `flow_fastapi_login` | Layered Python call path with `import … as` | Framework-shaped, not FastAPI itself |
| `flow_spring_login` | Java field.method() + layered roles | Not a real Spring Boot app |
| `inherit_repo` | Cross-file `extends` / `implements` / `*Impl` | Minimal |
| `psf/requests` (optional `--repo`) | Real Python library, public, stable | One language; we do not cherry-pick 30 repos yet |

Selection rule: **fixtures we own the gold for** + **one well-known public library** so ingest/incremental numbers are not synthetic. Expanding to 5–10 public repos and historical-PR impact ground truth is tracked below — not silently implied by current tables.

---

## Ground-truth construction

1. **Graph / flow / impact (fixtures)** — edges and must-include symbols written by hand from the fixture source, stored in `eval/gold/structure.json`. Matching: exact `symbol_ref` or unambiguous substring on both ends of a call edge; exact path pair for imports; exact child/parent + relation for inherit.
2. **Retrieval** — `gold_spans` in JSONL. A prediction hits gold when **paths match** and **line ranges overlap** (`eval/metrics.py`). AST chunks are not required to equal human spans.
3. **requests symbol queries** — gold line ranges are **resolved from the same ingest’s `definitions.json`** (class/function AST), not copied from memory. If a symbol cannot be resolved, that query is dropped (listed in the JSON report).
4. **Reviewer** — synthetic findings (hallucinated path, missing cite, malformed cite, symbol absent from snippet, plus one valid control).

**Planned, not in this snapshot:** historical PR as impact GT (files/functions actually touched by a signature-change PR vs `analyze_impact` prediction); LSP/tsc caller sets; 10–20 issue-fix tasks with/without MCP.

---

## Snapshot

Source: `eval/reports/latest.json` from `python -m eval.run_benchmarks --real-embed`  
Host: Windows / CPython **3.12.4** (`RepoScope/.venv`)  
Generated: **2026-08-15 06:19 UTC**  
Pytest: **131 passed** in 3.63s  
Retrieval embedding: **`sentence-transformers/all-MiniLM-L6-v2`**, in-memory cosine, **rerank off**

Two earlier attempts on this machine failed while downloading MiniLM (`os error 112` / disk full). This snapshot is the first complete `--real-embed` run after the model loaded.

### Structure quality

| Metric | Value | n |
|---|---:|---:|
| Gold-edge recall | **100%** (18/18) | curated must-resolve edges |
| Flow term coverage | **100%** | FastAPI + Spring login fixtures |
| Flow cases fully covered | **2/2** | 4 evidence steps each, all cited |
| Impact gold seeds | **1/1** | `Animal` → `Dog` |
| Reviewer catch rate | **100%** (4/4) | fabricated citations; valid control passed |

Missed gold edges: none in this run. **Do not read 100% as “complete call graph.”** The gold set is 18 edges we require, not every true edge in those programs.

### NL → tools (Spider-style)

Source: `eval/reports/tool_eval.json` (2026-08-15 07:18 UTC), gold `eval/gold/tools.jsonl`.

| Tool | n | Recall | Notes |
|---|---:|---:|---|
| `query_dependencies` | 6 | **0.83** | Fixtures 5/5; `Session.send` ↛ `HTTPAdapter.send` (static miss) |
| `trace_flow` | 3 | **0.67** | Login fixtures 2/2 ordered coverage; `requests` traced `Session.request` not `Session.send` |
| `analyze_impact` | 1 | **1.00** | `Animal` → `Dog` |

**Quote fixtures separately from `requests`.** On the 8 fixture tool cases recall is 1.0. The two `psf/requests` rows are the ones that fail — that is the point of a classic-repo gold set.

### MCP task utility (scripted, not a live agent)

Source: `eval/reports/mcp_tasks.json`. Policies are one tool call (`explore_first` / `flow_first` / `impact_first`).

| | Value |
|---|---:|
| Tasks | 5/5 passed |
| Mean steps | 1.0 |
| Includes | FastAPI/Spring login, Animal blast radius, `Session.send` explore pack |

Success = `must_files` / `must_symbols` appear in the tool JSON. This is **not** SWE-bench and not an LLM agent.

### Retrieval

N = **18** questions (13 fixture JSONL + 5 `requests` symbols resolved from the same ingest’s `definitions.json`). Matching: file path + overlapping lines.

| Mode | N | Recall@5 | Precision@5 |
|---|---:|---:|---:|
| vector | 18 | **100.0%** | 28.5% |
| bm25 | 18 | 72.2% | **35.8%** |
| hybrid (RRF) | 18 | 94.4% | 27.4% |

Hybrid vs vector relative ΔRecall@5: **-5.6%**

**Do not quote “Hybrid lifts retrieval” from this snapshot.** Dense MiniLM already sat at Recall@5 = 1.0 on this small, identifier-heavy mix; RRF pulled in BM25 ranks and *dropped* macro recall. BM25 still has the best precision. A larger, more semantic QA set (and/or rerank) is required before Hybrid is a headline.

`requests` gold spans resolved this run:

- `src/requests/sessions.py:557-653` (`Session.request`)
- `src/requests/sessions.py:752-829` (`Session.send`)
- `src/requests/adapters.py:634-748` (`HTTPAdapter.send`)
- `src/requests/api.py:74-87` (`get`)
- `src/requests/models.py:732-1184` (`Response`)

### Index / incremental

| Repo | files | LOC | full ms | incr. ms | incr. mode | cached ms | artifact |
|---|---:|---:|---:|---:|---|---:|---:|
| `sample_repo` | 6 | 47 | 21 | 8 | merge | 6 | 16 KB |
| `flow_fastapi_login` | 7 | 30 | 18 | 9 | merge | 5 | 10 KB |
| `flow_spring_login` | 3 | 47 | 12 | 7 | merge | 5 | 10 KB |
| `inherit_repo` | 8 | 52 | 29 | 9 | merge | 7 | 25 KB |
| `psf/requests` (shallow clone) | 50 | 14 556 | **3656** | **3552** | merge | **263** | 1.79 MB |

`requests` graph this ingest: **100 import / 867 call / 41 inherit** edges, 313 chunks.

**Read the incremental column carefully.** Merge *mode* fired after touching `sessions.py`, but wall-clock was **1.03×** vs full (3.55 s vs 3.66 s) — origin fan-out on that file is large enough that merge ≈ full. The number that *does* move is **cached** no-op: **263 ms vs 3656 ms (~14×)**. Do not claim “merge is 10× faster than full” from this row.

### Query latency (warm, FastAPI login fixture, 25 repeats)

Includes auto-sync of a cached tiny index. Not a BFS-only microbenchmark.

| Tool | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|
| `query_dependencies` | 37.1 | 85.7 | 95.5 |
| `analyze_impact` | 40.6 | 71.9 | 149.5 |
| `context_explore` | 49.1 | 75.4 | 94.4 |
| `trace_flow` | 38.2 | 61.6 | 90.9 |

**Superseded.** Most of this was a redundant artifact rewrite on every warm
query; see [Re-measurement, 2026-08-17](#re-measurement-2026-08-17) for the
current p50 of 8.8 ms.

### Token proxy

Estimate = chars/4. Not a live agent.

**Toy fixture (`flow_fastapi_login`, query “How does login work?”)** — MCP **loses**:

| Path | Tokens | Tool calls |
|---|---:|---:|
| `context_explore` | 1984 | 1 |
| grep + full-file reads (2 files) | 135 | 3 |

Ratio grep/MCP = **0.07×**. The pack is larger than the whole fixture. **Do not use this row in marketing.**

**Real library (`psf/requests`, query “How does Session.send an HTTP request?”)**:

| Path | Tokens | Tool calls |
|---|---:|---:|
| `context_explore` | 7011 | 1 |
| grep + full-file reads (46 files) | 120 823 | 47 |

Ratio grep/MCP = **17.2×** tool-call count 47 → 1. This is the row that is allowed in a README *with the proxy paragraph attached*. It is still not SWE-bench.

**Superseded.** This row predates the `HashEmbedder` determinism fix below and
is not reproducible. The current figures are 15.4× (legacy) and 20.4×
(`use_advanced_kg`) — see [Re-measurement, 2026-08-17](#re-measurement-2026-08-17).

### Token proxy A/B: `use_advanced_kg`

Same fixture, same query, same seeds; only `config.use_advanced_kg` differs.
Reproduce with `python -m eval.run_benchmarks --advanced-kg --ab`.

| Fixture | off (legacy) | on (cascade) | Reduction |
|---|---:|---:|---:|
| `flow_fastapi_login` | 1984 | 1526 | **23.1%** |
| `psf/requests` | 7852 | 5935 | **24.4%** |

The saving is deduplication, not truncation. `report_markdown` is dropped
because every fact in it is already in the structured fields, and a seed that
also appears in `must_read` drops its duplicated snippet while keeping its rank
and citation. Working against that, blast-radius hits gain a `file:line`
evidence span; the percentages above are net of that cost.

Gold-edge recall is unchanged at 18/18 in both modes, as are flow term
coverage, impact, reviewer catch rate, and Recall@5 — see
`eval/reports/legacy.md` and `eval/reports/advanced.md`.

### Reproducibility fix

`HashEmbedder` hashed tokens with the builtin `hash()`, which CPython salts per
process. Every CI/smoke retrieval number therefore moved run to run (observed
Recall@5 of 0.611 / 0.667 / 0.778 for identical inputs). It now uses blake2b
and repeated runs are bit-identical. **Retrieval rows measured before this fix
are not reproducible and should not be compared against current ones.**

---

## Re-measurement, 2026-08-17

Same host, `eval/reports/legacy.md` (defaults) and `eval/reports/advanced.md`.
Structure quality is identical to the 2026-08-15 snapshot — 18/18 gold edges,
2/2 flow cases, 4/4 reviewer catches — in every mode. What moved is perf, and
the reason is not "the refactor made everything faster", so the deltas are
itemized below rather than folded into the tables above.

### Perf, default config

| | 2026-08-15 | 2026-08-17 | Why |
|---|---:|---:|---|
| `requests` first index | 3656 ms | **1732 ms** | Run-to-run / FS-cache variance on the same machine; nothing in the ingest path was optimized |
| `requests` incremental (`merge`) | 3552 ms | 902 ms | Same |
| `context_explore` p50 (fixture) | 49.1 ms | **8.8 ms** | Real fix: the pipeline used to rewrite every artifact even when a warm query changed nothing. It now skips the write when the mode is `cached` and the artifacts are already on disk |

The p50 improvement applies to both KG modes and to both storage backends —
it is in `IngestionPipeline.run`, not behind a switch.

### Perf, `use_advanced_kg` on

| Corpus | First index | Comment-only re-index | Artifact |
|---|---:|---:|---:|
| `requests`, legacy | 1732 ms | 902 ms (`merge`) | 1.86 MB |
| `requests`, advanced | 5083 ms | **110 ms** (`structure_cached`) | 1.92 MB |

The cascade costs roughly 3× on a cold index: every call site now runs six
resolution strategies instead of one lookup. It buys back an 8× faster
re-index on edits that do not change structure, because the AST hash proves the
existing edges are still correct. Whether that trades well depends entirely on
your edit/index ratio — for an agent re-syncing a working tree constantly it
does, for a one-shot CI index it does not.

### Perf, `kg_storage=sqlite`

Same advanced mode, only the backend differs (`eval/reports/advanced_sqlite.md`).

| | JSON | SQLite |
|---|---:|---:|
| Artifact, `flow_fastapi_login` | 11 KB | 72 KB |
| Artifact, `psf/requests` | 1.92 MB | 2.06 MB |
| `context_explore` p50, fixture | 10.5 ms | **79.8 ms** |
| `requests` first index | 5083 ms | 4724 ms |
| `requests` comment-only re-index | 110 ms | 116 ms |

**SQLite is currently a loss, and we are saying so.** Indexing is a wash and
the artifact gap closes as the repo grows (72 KB vs 11 KB on a fixture is the
page/index floor; 7% on `requests`), but warm query latency is ~8× because
every tool call reopens the database and re-materialises the whole graph. On a
7-file fixture that fixed cost *is* the measurement. It is committed as
groundwork for SQL-side query pushdown, not as a speedup. Quality metrics are
identical to the JSON run.

### Token proxy shifted with the embedder fix

The `requests` ratio moved from **17.2×** to **15.4×** in legacy mode. This is
not a regression in the pack: `_facade` in the harness always uses
`HashEmbedder`, so changing its hash function changed which seeds retrieval
picks, which changed the snippets in the pack. The pre-fix 17.2× was drawn from
a per-process-salted ranking and is not reproducible. 15.4× (legacy) and 20.4×
(advanced) are.

## Roadmap (so this file does not freeze as a fixture-only story)

1. **Public-repo suite (5–10)** — `requests`, `httpx`, plus one JS and one Java repo, with published inclusion criteria (stars, license, size bucket). Same harness, extra gold JSONL.
2. **Historical-PR impact GT** — pick merged PRs that rename or change a function signature; gold = files/symbols actually modified; score `analyze_impact` recall@k.
3. **Caller accuracy vs compiler** — where LSP/tsc exists (TS packages), compare `callers` to compiler references (codescope method).
4. **Small agent eval (10–20 tasks)** — same prompt, with vs without MCP; record pass/fail, tool calls, tokens. Qualitative until n is honest.
5. **Do not** add a marketing leaderboard until (1) and (2) exist.

---

## Reproducing someone else’s machine

```text
git clone <this-repo> && cd RepoScope
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,retrieval]"
python -m eval.run_benchmarks --real-embed
diff -u BENCHMARKS.md eval/reports/latest.md   # narrative vs raw

# knowledge-graph modes (reports land under eval/reports/<prefix>.{json,md})
python -m eval.run_benchmarks --out-prefix legacy
python -m eval.run_benchmarks --advanced-kg --ab --out-prefix advanced
python -m eval.run_benchmarks --advanced-kg --kg-storage sqlite --out-prefix advanced_sqlite
```

If Hybrid lift is quoted from a HashEmbedder run, it is invalid. If token ratio is quoted without the proxy paragraph, it is invalid.
