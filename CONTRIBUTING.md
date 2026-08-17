# Contributing to RepoScope

Read the product boundary in the [README](README.md) first. PRs that turn RepoScope into a chat UI or a coding agent will be declined. PRs that make KnowledgeGraph / FlowTracer / ArchitectureAnalyzer more accurate, faster, or cover more languages are the point.

## Good first issues

These are the usual first contributions for this kind of engine:

1. **A new tree-sitter language** (see below) — Go, Rust, C/C++, Ruby, …
2. **A flow fixture** that reproduces a real framework’s routing or DI (copy `tests/fixtures/flow_fastapi_login` or `flow_spring_login`)
3. **A missed gold edge** — add the edge to `eval/gold/structure.json` and a unit test that fails on `main`
4. **Docs-only** — a task-level example in the README that you actually ran

Label suggestions for maintainers: `good first issue`, `language`, `flow-fixture`, `gold-edge`.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

Optional:

```bash
pip install -e ".[dev,retrieval]"   # MiniLM + (Windows) pinned torch
docker compose up -d                # Qdrant / Postgres / Redis
python -m eval.run_benchmarks --skip-remote
```

## Adding a language (highest-leverage path)

End-to-end, one language per PR, fixture-backed.

1. **Grammar** — add the `tree-sitter-<lang>` package in `pyproject.toml`.
2. **Register** — `app/parsing/languages.py`:
   - `AST_LANGUAGES`
   - `SUPPORTED_EXTENSIONS`
   - `_language_object()` / `get_parser()`
3. **Definition query** — `app/parsing/ast_parser.py`: a tree-sitter query capturing `@name` + `@def` for functions, classes, methods. Register it in `_QUERIES`. Extend `_CLASS_NODE_TYPES` / `_METHOD_NODE_TYPES` / `_FUNCTION_NODE_TYPES` if the grammar uses new node types. Extract `bases` for inherit if the language has them.
4. **Imports + calls** — `app/graph/builder.py`: import resolution (regex or AST) into the per-file import map; call-site extraction if the generic `name(` / `recv.method(` fallback is wrong for this language. The builder has two resolution paths — the legacy one and the cascade behind `use_advanced_kg` (`SymbolResolver.resolve_call`). A new language must work in both; the cascade needs the call site's line number and, for `recv.method()`, the receiver.
5. **Fixture** — `tests/fixtures/<lang>_repo/` with at least:
   - two files that import each other
   - one cross-file call
   - inherit/implements if the language has it
6. **Tests** — parser extracts the right line ranges; graph has the import/call/inherit edges; add those edges to `eval/gold/structure.json`.
7. **Docs** — one line on language coverage in the README.

Do not add a language without a fixture. “Works on my private repo” is not reviewable.

## Flow / architecture contributions

- Flow: new call-resolution heuristics + a fixture that fails without them (`tests/helpers_flow.py` pattern).
- Architecture: new detectors in `app/intelligence/architecture/patterns.py` with tests; `unknown` must remain a valid primary pattern.

## Before opening a PR

1. Tests for every engine you touch. No new behavior without a fixture-backed test.
2. `pytest -q` green.
3. Evidence stays evidence: FlowTracer / ArchitectureAnalyzer claims still map to `file:line` or a graph edge.
4. Do not commit `data/`, `eval/reports/`, or generated indexes.
5. One concern per PR.

If you touch graph building, ingestion, or artifact IO, check both knowledge-graph
modes — the optional paths are off by default, so a plain `pytest` run does not
prove they still work:

```bash
python -m eval.run_benchmarks --skip-remote --out-prefix legacy
python -m eval.run_benchmarks --skip-remote --advanced-kg --kg-storage sqlite --out-prefix advanced
```

Gold-edge recall, flow coverage, and reviewer catch rate must match between the
two. A new switch is only worth having if turning it off restores the old
behaviour exactly.

If you change gold or the harness, attach `eval/reports/latest.md` (not committed) in the PR description so numbers can be checked.

## Commit / CI

Conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`) are appreciated. CI runs `pytest` on Python 3.11 and 3.12 — that is the gate. `ruff check` also runs but is currently non-blocking; keep new code clean anyway so it can be made blocking.

## Security

Do not open a public issue for a vulnerability. Use a GitHub security advisory, or email the maintainer.

## Conduct

Be respectful, assume good faith. Maintainers will close out-of-scope PRs (chat UI, auto-PRs, unbounded agent loops) — that is focus, not a judgment of the contributor.
