# Retrieval Mode Comparison

- Generated: 2026-07-24 08:58 UTC
- Dataset: `D:/B/1_VSCode/RepoScope/eval/datasets/qa_dataset.jsonl`
- Vector backend: `inmemory/cosine`
- Embedding: `sentence-transformers/all-MiniLM-L6-v2`
- Fusion: `rrf`
- Rerank: `off` (BAAI/bge-reranker-base)
- Hash embedder (CI-only): `True`

> Matching rule: a prediction hits gold when **file path matches** and **line ranges overlap**
> (not exact string equality).

## Macro results

| Mode | N | Recall@5 | Precision@5 |
|---|---:|---:|---:|
| vector | 5 | 100.0% | 20.0% |
| bm25 | 5 | 100.0% | 26.0% |
| hybrid | 5 | 100.0% | 20.0% |

- Hybrid vs Vector relative ΔRecall@5: **+0.0%**
- Hybrid vs Vector relative ΔPrecision@5: **+0.0%**

## By question type

### vector

| question_type | n | Recall@5 | Precision@5 |
|---|---:|---:|---:|
| dependency | 3 | 100.0% | 20.0% |
| refactor | 1 | 100.0% | 20.0% |
| summary | 1 | 100.0% | 20.0% |

### bm25

| question_type | n | Recall@5 | Precision@5 |
|---|---:|---:|---:|
| dependency | 3 | 100.0% | 20.0% |
| refactor | 1 | 100.0% | 50.0% |
| summary | 1 | 100.0% | 20.0% |

### hybrid

| question_type | n | Recall@5 | Precision@5 |
|---|---:|---:|---:|
| dependency | 3 | 100.0% | 20.0% |
| refactor | 1 | 100.0% | 20.0% |
| summary | 1 | 100.0% | 20.0% |


## Notes

- Fill these numbers into `eval/reports/evaluation_report_template.md`.
- Prefer `vector_backend=qdrant` + real embeddings for resume / interview figures.
- Do **not** quote hash-embedder numbers as official Hybrid gains.
