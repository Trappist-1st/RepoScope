"""Shared clone / path helpers for eval harnesses."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_repo(repo_path: str | None, repo_url: str | None, workspace: Path) -> Path:
    if repo_path:
        path = Path(repo_path)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists() and path.is_dir():
            return path.resolve()
    if not repo_url:
        raise FileNotFoundError(f"missing repo: path={repo_path!r} url={repo_url!r}")
    workspace.mkdir(parents=True, exist_ok=True)
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = workspace / name
    if dest.exists():
        return dest.resolve()
    subprocess.run(["git", "clone", "--depth", "1", repo_url, str(dest)], check=True)
    return dest.resolve()


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows
