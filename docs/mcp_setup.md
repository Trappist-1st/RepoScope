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

## Knowledge-graph switches (optional, off by default)

Both are independent and reversible; turning them off restores the original
pipeline exactly. Details and measured trade-offs: [architecture.md](architecture.md).

| Env var | Default | Effect |
|---|---|---|
| `REPOSCOPE_USE_ADVANCED_KG` | `false` | Cascading call resolution, per-edge confidence and `file:line`, AST structure hashing, ~23% smaller `context_explore` payload |
| `REPOSCOPE_KG_STORAGE` | `json` | `sqlite` puts graph + chunks in one `reposcope.db` |
| `REPOSCOPE_KG_MIN_CONFIDENCE` | `0.5` | Blast-radius pruning threshold; only read when the advanced flag is on |

```json
"env": { "REPOSCOPE_USE_ADVANCED_KG": "true" }
```

Flipping `REPOSCOPE_USE_ADVANCED_KG` forces a knowledge-graph rebuild on the
next call, so the artifact never mixes cascade-resolved and legacy edges.
`REPOSCOPE_KG_STORAGE` can be switched either way without a reindex.

## Audit warning

If `REPOSCOPE_DATABASE_URL` is unset, `agent_runs` is **in-memory**. Every tool `meta.warnings` includes `audit_backend: in_memory`. Restarts wipe history. Fine for local demo; not durable.

## Agent hint

Prefer `context_explore` for how-it-works / edit-prep. Inspect `meta.warnings` and `meta.graph_update_mode`. Do not re-verify every hit with grep when the citation is `file:line` from the graph.
