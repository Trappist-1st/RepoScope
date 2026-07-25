# RepoScope Evaluation Report Template (Phase 6)

> Fill this after running the harness on your real annotated set.
> Leave placeholders (`TBD` / `_`) empty until you have numbers from a
> **Qdrant + real embedding** run — do not paste hash-embedder smoke numbers
> into a resume.

- Report date: YYYY-MM-DD
- Dataset: `eval/datasets/qa_dataset.jsonl` (N = __ questions, __ repos)
- Vector backend: `qdrant/cosine` / `inmemory/cosine`
- Embedding model: `________________`
- Rerank model: `________________` (on / off)
- Fusion: `rrf` / `weighted`
- Matching rule: file path equal + line-range **overlap**

---

## 1. Retrieval — mode comparison

Source: `python -m eval.run_retrieval_eval --compare-modes` → `eval/reports/retrieval_compare.md`

| Mode | N | Recall@5 | Precision@5 |
|---|---:|---:|---:|
| vector (dense only) | TBD | TBD | TBD |
| bm25 (sparse only) | TBD | TBD | TBD |
| hybrid | TBD | TBD | TBD |

### Relative lift (Hybrid vs Vector)

| Metric | Relative Δ |
|---|---:|
| Recall@5 | TBD% |
| Precision@5 | TBD% |

**One-sentence takeaway:**  
_Hybrid improves ____ because ____ (e.g. BM25 recovers exact identifiers that dense misses)._

### By question type (Hybrid)

| question_type | n | Recall@5 | Precision@5 |
|---|---:|---:|---:|
| summary | TBD | TBD | TBD |
| dependency | TBD | TBD | TBD |
| refactor | TBD | TBD | TBD |

---

## 2. Human evaluation — citation & grounding

Source: `python -m eval.run_human_eval -n 20` → `eval/reports/human_eval_summary.md`

| Metric | Value |
|---|---:|
| Findings labeled | TBD |
| Unique analyze samples | TBD |
| Citation accuracy | TBD% |
| Conclusion grounding accuracy | TBD% |
| Both OK rate | TBD% |

**Rubric (keep consistent across annotators):**
- Citation accurate = cited `path:start-end` exists **and** is topically related
- Conclusion grounded = claim follows from the cited spans without unsupported leaps

**Failure modes observed (fill with real examples):**
1. _e.g. citation points to import line but claim describes implementation_
2. _e.g. graph-expanded evidence overstated as direct_
3. _

---

## 3. System performance (optional but resume-friendly)

| Metric | Value |
|---|---:|
| Analyze latency P50 | TBD ms |
| Analyze latency P95 | TBD ms |
| Avg tokens after Context Engine | TBD |
| Context trim ratio (real repo) | TBD% (see `context_budget_exp.md`) |
| Incremental vs full index time | TBD / TBD |

---

## 4. Context Engineering snapshot

Source: `python -m eval.run_context_budget_exp --repo <real-repo>`

| Metric | Value |
|---|---:|
| Repo | TBD |
| Approx source LOC | TBD |
| Tokens before trim | TBD |
| Tokens after assemble | TBD |
| Trim ratio | TBD% |

---

## 5. Reviewer / grounding check anecdotes

| Case | What happened | Outcome |
|---|---|---|
| Hallucinated citation | _Reviewer rejected / retry_ | |
| Expanded-only soft downgrade | _confidence lowered, still shown_ | |
| Pass after retry with hints | _retrieve reformulated_ | |

---

## 6. Resume-ready bullets (draft from real numbers only)

- Hybrid Retrieval lifted Recall@5 by **~X%** relative to dense-only on an N-question annotated set spanning M repos.
- Human review of K analyze findings measured **Y%** citation accuracy and **Z%** grounding accuracy.
- Context Engineering cut prompt tokens by **~T%** on a ~LOC-scale real repository under budget B.

---

## Appendix — how numbers were produced

```powershell
# 1) Annotate gold spans in eval/datasets/qa_dataset.jsonl
# 2) Retrieval compare (official: qdrant + real embeddings)
$env:REPOSCOPE_VECTOR_BACKEND = "qdrant"
python -m eval.run_retrieval_eval --compare-modes --backend qdrant

# 3) Dump / collect analyze outputs, then label
python -m eval.run_human_eval --input path\to\analyze_dumps.jsonl -n 20

# 4) Context budget (real repo)
python -m eval.run_context_budget_exp --repo https://github.com/psf/requests.git
```

Raw artifacts:
- `eval/reports/retrieval_compare.md` / `.json`
- `eval/reports/human_labels.jsonl`
- `eval/reports/human_eval_summary.md`
- `eval/reports/context_budget_exp.md`
