# RepoScope MCP Server setup

RepoScope exposes eight MCP tools over **stdio**:

| Tool | Purpose |
|---|---|
| `get_initial_context` | **Start here.** README + profile + core modules + core file source (the "launchpad") |
| `get_repo_summary` | Architecture summary with citations |
| `search_code` | Hybrid BM25 + vector search over indexed code chunks |
| `view_source` | Read a symbol, a line range, or a whole file (+ outline) |
| `query_dependencies` | Callers / callees / imports for a symbol |
| `trace_flow` | Evidence-backed call path for a business flow |
| `analyze_architecture` | Modules / patterns / coupling / profile |
| `suggest_refactor` | Refactor suggestions for a file |

Suggested exploration order for an unfamiliar repo: `get_initial_context` →
`search_code` / `analyze_architecture` → `view_source` / `query_dependencies`
to drill into specifics.

## Run locally

```powershell
cd D:\B\1_VSCode\RepoScope
.venv\Scripts\activate
pip install -e ".[dev,api,mcp]"
python -m app.mcp.server
```

## Claude Code / Cursor config example

Add to your MCP settings (path names vary by client):

```json
{
  "mcpServers": {
    "reposcope": {
      "command": "D:\\B\\1_VSCode\\RepoScope\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp.server"],
      "cwd": "D:\\B\\1_VSCode\\RepoScope",
      "env": {
        "REPOSCOPE_ARTIFACT_DIR": "data/artifacts",
        "REPOSCOPE_WORKSPACE_ROOT": "data/workspace"
      }
    }
  }
}
```

Optional persistent audit / live state:

```json
"env": {
  "REPOSCOPE_DATABASE_URL": "postgresql://reposcope:reposcope@localhost:5432/reposcope",
  "REPOSCOPE_REDIS_URL": "redis://localhost:6379/0"
}
```

## MCP Inspector

```powershell
npx @modelcontextprotocol/inspector python -m app.mcp.server
```

Then call tools from the Inspector UI.

## Important: audit backend degradation

If `REPOSCOPE_DATABASE_URL` is **not** set, RepoScope uses an **in-memory** `agent_runs` store.

- Every tool response includes `meta.warnings` containing roughly:
  `audit_backend: in_memory (non-persistent, will be lost on restart)`
- The same notice appears on `GET /health` for the FastAPI server.
- **This is intentional for local demo**, but it is **not** durable audit. Restarting the MCP process wipes run history.
- For interview / “production-minded” demos, start Postgres via `docker compose up -d` and set `REPOSCOPE_DATABASE_URL`.

Similarly, without Redis, live run state is in-memory and non-persistent.

## Example tool calls

**Initial context (call this first)**

```json
{
  "repo_url": "D:/B/1_VSCode/RepoScope/tests/fixtures/sample_repo",
  "top_k_modules": 8,
  "top_k_core_files": 5
}
```

No README in the repo, or no readable core files? Both surface as explicit
`meta.warnings` / `warnings` entries — never a silent empty section.

**Search**

```json
{
  "repo_url": ".../sample_repo",
  "query": "greet helper",
  "top_k": 10
}
```

Zero ranked hits triggers a fallback to a diversity sample of indexed chunks
(`notes` explains this); results always carry a `file:line` citation.

**View source**

```json
{
  "repo_url": ".../sample_repo",
  "file_path": "py_pkg/a.py",
  "symbol_name": "greet"
}
```

Precedence is `symbol_name` > (`start_line`, `end_line`) > whole file. Omit
all three to get the file (capped ~400 lines) plus an `outline` of its
top-level definitions, so you can target a specific symbol next.

**Summary**

```json
{
  "repo_url": "D:/B/1_VSCode/RepoScope/tests/fixtures/sample_repo",
  "question": "Summarize architecture"
}
```

Check `meta.indexing_status` ∈ `cached | incremental | full_reindex` and `meta.warnings`.

**Dependencies**

```json
{
  "repo_url": ".../sample_repo",
  "symbol_name": "greet",
  "direction": "both",
  "limit": 20
}
```

If `query.resolved_refs` has multiple entries, read `notes` — pass `file::symbol` to disambiguate.

**Refactor**

```json
{
  "repo_url": ".../sample_repo",
  "file_path": "py_pkg/a.py",
  "focus": "coupling"
}
```

Expanded-only evidence is capped at `confidence=medium` and includes `expansion_reason` when available.
