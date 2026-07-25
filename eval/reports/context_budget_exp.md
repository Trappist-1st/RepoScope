# Context Engineering Budget Experiment

- Generated: 2026-07-24 08:42 UTC
- Repo source: `https://github.com/psf/requests.git`
- Local path: `D:\B\1_VSCode\RepoScope\data\exp_workspace\requests`
- Indexed source files: **20**
- Approximate source LOC (supported langs, excluding tests/venv): **6403**
- Vector backend for retrieval candidates: `inmemory/cosine` + HashEmbedder
  (allocator itself is backend-agnostic; re-run with Qdrant + real embeddings for official resume numbers)
- Query: How does the Session object send HTTP requests and handle adapters?

## Budget config

- Total budget B: **4000**
- Weights: entry=0.2, graph=0.3,
  relevance=0.4, tier=0.1
- G mix: file=0.5, symbol=0.5
- Buckets: code=0.7, graph=0.15,
  history=0.1, reserve=0.05
- History policy: sliding window (latest 1 round), not priority-trimmed

## Results

| Metric | Value |
|---|---|
| Candidates (hits + expanded) | 44 |
| Tokens before trim (code+reserve approx) | **57137** |
| Tokens after assemble | **3372** |
| Trim ratio | **94.1%** |
| Code tokens | 1085 |
| Graph summary tokens | 2257 |
| History tokens | 0 |
| Reserve tokens | 30 |
| Kept chunks | 3 |
| Dropped chunks | 41 |

## Top kept (by priority)

| citation | priority | E | G | R | T | file/sym refs |
|---|---:|---:|---:|---:|---:|---|
| `src/requests/cookies.py:135-150` | 0.603 | 0 | 0.478 | 0.899 | 1.00 | 9/4 |
| `src/requests/api.py:74-87` | 0.576 | 0 | 0.545 | 0.781 | 1.00 | 1/29 |
| `src/requests/api.py:24-71` | 0.565 | 0 | 0.218 | 1.000 | 1.00 | 1/10 |

## Lowest dropped

| citation | priority | E | G | R | T |
|---|---:|---:|---:|---:|---:|
| `tests/test_requests.py:2536-2602` | 0.055 | 0 | 0.000 | 0.000 | 0.55 |
| `tests/test_requests.py:2679-2689` | 0.055 | 0 | 0.000 | 0.000 | 0.55 |
| `src/requests/api.py:90-99` | 0.069 | 0 | 0.045 | 0.000 | 0.55 |
| `src/requests/api.py:171-180` | 0.069 | 0 | 0.045 | 0.000 | 0.55 |
| `tests/test_lowlevel.py:127-189` | 0.248 | 0 | 0.000 | 0.369 | 1.00 |
| `tests/test_requests.py:2740-2747` | 0.251 | 0 | 0.000 | 0.378 | 1.00 |
| `tests/test_requests.py:2750-2757` | 0.253 | 0 | 0.000 | 0.383 | 1.00 |
| `tests/test_lowlevel.py:364-404` | 0.255 | 0 | 0.000 | 0.388 | 1.00 |
| `tests/test_utils.py:376-405` | 0.257 | 0 | 0.000 | 0.392 | 1.00 |
| `docs/_themes/flask_theme_support.py:7-86` | 0.260 | 0 | 0.017 | 0.388 | 1.00 |

## Notes for resume

- Use this table's before/after/ratio only if LOC and backend match what you claim.
- Prefer re-running with `--repo https://github.com/django/django.git` (or Spring Boot)
  and `REPOSCOPE_VECTOR_BACKEND=qdrant` + real embeddings for the authoritative figure.
- Do **not** replace this with synthetic duplicated fixtures.
