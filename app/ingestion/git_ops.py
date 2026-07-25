from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError


@dataclass
class RepoCheckout:
    repo_id: str
    local_path: Path
    commit_hash: str
    source: str


def _is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https", "git", "ssh"} or source.startswith("git@")


def stable_repo_id(source: str) -> str:
    normalized = source.strip().replace("\\", "/").rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def ensure_repo(source: str, workspace_root: Path | None = None) -> RepoCheckout:
    """
    Clone/update a remote repo into workspace, or attach a local path.
    """
    from app.config import settings

    root = Path(workspace_root or settings.workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    source = source.strip()
    if _is_url(source):
        return _ensure_remote(source, root)
    return _ensure_local(Path(source).expanduser().resolve(), source)


def _ensure_remote(url: str, workspace_root: Path) -> RepoCheckout:
    repo_id = stable_repo_id(url)
    local_path = workspace_root / repo_id

    if local_path.exists() and (local_path / ".git").exists():
        repo = Repo(local_path)
        try:
            repo.remotes.origin.fetch()
            # Prefer current branch tip after pull; fall back to fetch only
            try:
                repo.remotes.origin.pull(rebase=False)
            except GitCommandError:
                pass
        except GitCommandError:
            pass
    else:
        if local_path.exists():
            # Non-git leftover — remove only if empty-ish? Safer to use fresh path.
            raise FileExistsError(f"Workspace path exists but is not a git repo: {local_path}")
        Repo.clone_from(url, local_path)

    repo = Repo(local_path)
    commit_hash = repo.head.commit.hexsha
    return RepoCheckout(repo_id=repo_id, local_path=local_path, commit_hash=commit_hash, source=url)


def _ensure_local(path: Path, original_source: str) -> RepoCheckout:
    if not path.exists():
        raise FileNotFoundError(f"Local path not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Local path is not a directory: {path}")

    repo_id = stable_repo_id(str(path))
    commit_hash = ""
    try:
        repo = Repo(path, search_parent_directories=False)
        commit_hash = repo.head.commit.hexsha
    except InvalidGitRepositoryError:
        commit_hash = ""

    return RepoCheckout(
        repo_id=repo_id,
        local_path=path,
        commit_hash=commit_hash,
        source=original_source,
    )
