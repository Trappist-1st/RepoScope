# RepoScope Evaluation Harness (Phase 6)

This folder is the **source of truth for resume / interview numbers**.
Smoke fixtures are included; replace them with your own 15–20 repos before claiming lifts.

## Layout

```
eval/
├── datasets/
│   ├── qa_dataset.jsonl          # annotated retrieval questions (you fill)
│   ├── analyze_samples.jsonl     # demo analyze dumps for human-eval practice
│   └── retrieval_qa.jsonl        # legacy Phase-2 skeleton (still loadable)
├── test_repos.yaml               # checklist of repos you plan to evaluate
├── metrics.py                    # Recall@k / Precision@k (overlap matching)
├── dataset.py                    # JSONL loader + schema normalization
├── run_retrieval_eval.py         # vector vs BM25 vs Hybrid comparison
├── run_human_eval.py             # interactive citation/grounding labeling
├── run_context_budget_exp.py     # Context Engine budget experiment
└── reports/
    ├── evaluation_report_template.md   # fill with real numbers
    ├── retrieval_compare.md            # auto-written by retrieval eval
    └── human_eval_summary.md           # auto-written by human eval
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
