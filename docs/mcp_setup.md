# MCP client setup

RepoScope speaks **stdio** MCP: `python -m app.mcp.server`.

Tool list, arguments, and when to use each tool: **[mcp-tools.md](mcp-tools.md)**.

## Run

```bash
pip install -e ".[dev]"
python -m app.mcp.server
```

Inspector:

```bash
npx @modelcontextprotocol/inspector python -m app.mcp.server
```

## Cursor / Claude Code / Windsurf / Codex

Point `command` at the **venv Python** (absolute path) and `args` at `-m app.mcp.server`.

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

Windows example: `.venv\\Scripts\\python.exe`. A workspace-relative template lives at `.cursor/mcp.json`.

Optional durable audit / live state:

```json
"env": {
  "REPOSCOPE_DATABASE_URL": "postgresql://reposcope:reposcope@localhost:5432/reposcope",
  "REPOSCOPE_REDIS_URL": "redis://localhost:6379/0"
}
```

## Audit warning

If `REPOSCOPE_DATABASE_URL` is unset, `agent_runs` is **in-memory**. Every tool `meta.warnings` includes `audit_backend: in_memory`. Restarts wipe history. Fine for local demo; not durable.

## Agent hint

Prefer `context_explore` for how-it-works / edit-prep. Inspect `meta.warnings` and `meta.graph_update_mode`. Do not re-verify every hit with grep when the citation is `file:line` from the graph.
