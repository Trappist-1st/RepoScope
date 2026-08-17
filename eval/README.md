# RepoScope Evaluation Harness

Quoted numbers live in [`../BENCHMARKS.md`](../BENCHMARKS.md). This folder is how you **reproduce** them.

## Three-layer path

| Layer | Metrics | Gold | Command |
|---|---|---|---|
| **1. Retrieval quality** | Precision@K, Recall@K, **MRR@K** | [`datasets/qa_dataset.jsonl`](datasets/qa_dataset.jsonl) — NL → `file:line` (fixtures + `psf/requests`) | `python -m eval.run_retrieval_eval --compare-modes` · add `--with-rerank` for hybrid+rerank · `--real-embed` via not passing `--hash-embedder` |
| **2. NL → structured tools** (Spider/BIRD-style) | Caller/callee recall; flow **ordered coverage**; citation faithfulness | [`gold/tools.jsonl`](gold/tools.jsonl) | `python -m eval.run_tool_eval` |
| **3. MCP task utility** | Task success rate, tool-call steps | [`gold/mcp_tasks.jsonl`](gold/mcp_tasks.jsonl) | `python -m eval.run_mcp_tasks` |
| **Perf / efficiency** | files/s, LOC/s, artifact bytes, query p50/p95 | [`repos.yaml`](repos.yaml) buckets small / medium / large | `python -m eval.run_perf --bucket small` |

Context-explore *faithfulness* is the `cited` flag on flow steps plus Reviewer catch rate in `run_benchmarks` (RAGAS-like: every claim must map to `file:line`; we do **not** run an LLM-as-judge).

Smoke everything except large ingest:

```bash
python -m eval.run_benchmarks --skip-remote
python -m eval.run_tool_eval --skip-remote
python -m eval.run_mcp_tasks --skip-remote
python -m eval.run_retrieval_eval --compare-modes --hash-embedder
python -m eval.run_perf --bucket small --skip-latency
```

Official retrieval (MiniLM): drop `--hash-embedder`. Medium/large clones (`sqlalchemy`, `django`) are opt-in — do not quote empty buckets.

## Knowledge-graph modes (A/B)

`run_benchmarks` runs one KG mode per invocation and records it under `kg_mode`
in the report, so a mixed run can never be mistaken for a comparison.

| Flag | Default | Purpose |
|---|---|---|
| `--advanced-kg` | off | Run with `config.use_advanced_kg` on |
| `--kg-storage {json,sqlite}` | `json` | Artifact backend for this run |
| `--ab` | off | Also measure the token proxy in the *other* KG mode and print the delta |
| `--out-prefix NAME` | `latest` | Write `eval/reports/NAME.{json,md}` instead of overwriting `latest` |

```bash
python -m eval.run_benchmarks --out-prefix legacy
python -m eval.run_benchmarks --advanced-kg --ab --out-prefix advanced
python -m eval.run_benchmarks --advanced-kg --kg-storage sqlite --out-prefix advanced_sqlite
```

Quality metrics (gold-edge recall, flow coverage, impact, reviewer catch rate)
must come out identical across all three. If they do not, the switch has
changed behaviour it was not supposed to change — that is the regression signal.

**Reproducibility.** `HashEmbedder` used to hash tokens with the builtin
`hash()`, which CPython salts per process, so smoke Recall@5 moved run to run
(0.611 / 0.667 / 0.778 for identical inputs were observed). It now uses blake2b
and repeated runs are bit-identical. Retrieval rows recorded before that fix are
not comparable with current ones.

## Layout

```
eval/
├── gold/
│   ├── structure.json            # must-resolve graph/flow/impact edges
│   ├── tools.jsonl               # NL → query_dependencies / trace_flow / impact
│   └── mcp_tasks.jsonl           # scripted agent goals
├── repos.yaml                    # classic public repos by size bucket
├── datasets/qa_dataset.jsonl     # retrieval gold (file + overlapping lines)
├── run_retrieval_eval.py         # vector vs BM25 vs hybrid [+ rerank]
├── run_tool_eval.py
├── run_mcp_tasks.py
├── run_perf.py
├── run_benchmarks.py             # combined snapshot; --advanced-kg / --kg-storage / --ab
└── reports/                      # gitignored (latest.*, or whatever --out-prefix names)
```

## 1. QA dataset schema (JSONL)

Each line is one JSON object:

| Field | Required | Description |
|---|---|---|
| `id` | yes | Stable id, e.g. `requests-003` |
| `repo_url` | one of url/path | Git URL (shallow-cloned on demand) |
| `repo_path` | one of url/path | Local path relative to repo root |
| `question` | yes | Natural-language question |
| `question_type` | yes | `summary` \| `dependency` \| `refactor` |
| `gold_spans` | yes | Expected hit spans (see below) |
| `notes` | no | Annotator notes |

`gold_spans` accepts either form:

```json
"gold_spans": [
  {"file_path": "requests/sessions.py", "start_line": 645, "end_line": 720}
]
```

```json
"gold_spans": ["requests/sessions.py:645-720"]
```

**Matching rule used by the harness:** a prediction hits gold when the **file path
matches** and the **line ranges overlap**. Exact string equality is not required
(AST chunks rarely equal human spans).

Chinese aliases for `question_type` are accepted: `摘要类`→summary, `依赖查询类`→dependency,
`重构建议类`→refactor.

### Annotation tips

- Prefer the **smallest span that answers the question**, not the whole file.
- For multi-hop dependency questions, list **all** spans that should be retrieved.
- Keep `question_type` balanced across the set so per-type tables are meaningful.

## 2. Retrieval evaluation (3-way compare)

```powershell
# Smoke (hash embedder — NOT for resume numbers)
python -m eval.run_retrieval_eval --compare-modes --hash-embedder

# Official (Qdrant + real embeddings)
$env:REPOSCOPE_VECTOR_BACKEND = "qdrant"
python -m eval.run_retrieval_eval --compare-modes --backend qdrant
```

Outputs:

- Console comparison table (Recall@5 / Precision@5)
- `eval/reports/retrieval_compare.md`
- `eval/reports/retrieval_compare.json`

## 3. Human evaluation (citation + grounding)

```powershell
# Practice on bundled demo dumps
python -m eval.run_human_eval --input eval/datasets/analyze_samples.jsonl -n 3

# Or load persisted agent_runs (requires Postgres)
$env:REPOSCOPE_DATABASE_URL = "postgresql://reposcope:reposcope@localhost:5432/reposcope"
python -m eval.run_human_eval --from-audit -n 20
```

For each finding you answer:

1. Is the **citation accurate**? (exists + relevant)
2. Is the **conclusion grounded**? (claim follows from citations)

Labels append to `eval/reports/human_labels.jsonl`; summary → `human_eval_summary.md`.

### Dumping analyze results for labeling

Any JSON/JSONL with a `findings` list works. Convenient shapes:

- Workflow `report_json` / `WorkflowResult.model_dump()`
- `AgentRunRecord.model_dump()` from the audit store
- MCP tool payloads that nest findings under `result.findings`

## 4. Final report

Copy numbers into:

[`eval/evaluation_report_template.md`](evaluation_report_template.md)

## Repo checklist

Track planned repos in [`eval/test_repos.yaml`](test_repos.yaml).
