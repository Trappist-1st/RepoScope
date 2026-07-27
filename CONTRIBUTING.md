# Contributing to RepoScope

Thanks for considering a contribution. RepoScope is deliberately scoped —
please read the "Product boundary" section in the [README](README.md) before
proposing a feature. PRs that turn RepoScope into a chat UI or a coding agent
will be declined regardless of quality; PRs that make the three core engines
(KnowledgeGraph / FlowTracer / ArchitectureAnalyzer) more accurate, faster,
or cover more languages are very welcome.

## Ways to contribute

- **Language support** — new tree-sitter grammars (Go, Rust, C/C++, Ruby, …)
  under `app/parsing/`, with fixtures under `tests/fixtures/`.
- **Flow Trace accuracy** — new call-resolution heuristics, fixtures that
  reproduce a real framework's routing/DI conventions (see
  `tests/fixtures/flow_spring_login`, `flow_fastapi_login` for the pattern).
- **Architecture patterns** — new pattern detectors in
  `app/intelligence/architecture/`.
- **Bug reports** — please include the repo (or a minimal fixture) that
  reproduces the issue; "wrong output on my private repo" without a
  reproducible case is hard to act on.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest -q
```

Optional retrieval / infra extras:

```bash
pip install -e ".[dev,retrieval]"
docker compose up -d
```

## Before opening a PR

1. **Add or update tests.** Every engine (KnowledgeGraph, FlowTracer,
   ArchitectureAnalyzer) is fixture-driven — see `tests/fixtures/` for
   examples of the expected shape. New behavior without a fixture-backed
   test won't be merged.
2. **Run the full suite:** `pytest -q`.
3. **Keep evidence-backed outputs evidence-backed.** If you touch
   FlowTracer or ArchitectureAnalyzer, every claim in the output should
   still be traceable to a `file:line` or a graph edge — no
   unattributed/LLM-hallucinated findings.
4. **Don't commit generated artifacts.** `data/`, `eval/reports/`, and any
   `*_retest*` / `exp_*` directories are for local runs only — they should
   stay out of git (see `.gitignore`).
5. **Keep PRs focused.** One engine or one concern per PR is much easier to
   review than a sweep across the codebase.

## Commit / PR conventions

- Conventional-commit-style prefixes are appreciated (`feat:`, `fix:`,
  `docs:`, `test:`, `refactor:`) but not enforced by CI yet.
- Link the issue you're addressing, or describe the motivating use case if
  there isn't one yet.
- CI (`pytest` across supported Python versions + `ruff check`) must be
  green before review.

## Reporting security issues

Please do not open a public issue for a security vulnerability. Open a
private security advisory on GitHub instead (Security tab → "Report a
vulnerability"), or email the maintainer directly if that option isn't
available yet.

## Code of conduct

Be respectful, assume good faith, keep discussion focused on the technical
merits. Maintainers reserve the right to close issues/PRs that don't fit the
project's scope (see README's "out of scope" list) — this is about project
focus, not about the contributor.
