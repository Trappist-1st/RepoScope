"""Optional background watcher that keeps a repo index fresh.

Default backend is a portable mtime/size poller (no extra deps).
Install ``watchdog`` and it will be used automatically when available:

    pip install watchdog
    python -m app.watch --repo /path/to/project

Environment:
  REPOSCOPE_WATCH_DEBOUNCE_MS   debounce window (default 2000)
  REPOSCOPE_WATCH_POLL_MS       poll interval for fallback (default 1500)
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.config import settings
from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion import IngestionPipeline
from app.parsing.languages import SUPPORTED_EXTENSIONS

logger = logging.getLogger("reposcope.watch")


def _debounce_ms() -> int:
    raw = os.environ.get("REPOSCOPE_WATCH_DEBOUNCE_MS", "2000")
    try:
        return max(100, min(int(raw), 60_000))
    except ValueError:
        return 2000


def _poll_ms() -> int:
    raw = os.environ.get("REPOSCOPE_WATCH_POLL_MS", "1500")
    try:
        return max(200, min(int(raw), 30_000))
    except ValueError:
        return 1500


def _is_watched(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    if any(part in settings.exclude_dirs for part in rel_parts):
        return False
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """rel_path -> (mtime_ns, size)."""
    out: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or not _is_watched(path, root):
            continue
        try:
            st = path.stat()
            rel = path.relative_to(root).as_posix()
            out[rel] = (getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)), st.st_size)
        except OSError:
            continue
    return out


class DebouncedSync:
    def __init__(self, sync_fn: Callable[[], None], debounce_ms: int) -> None:
        self._sync_fn = sync_fn
        self._debounce = debounce_ms / 1000.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._pending_reason = ""

    def poke(self, reason: str = "change") -> None:
        with self._lock:
            self._pending_reason = reason
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            reason = self._pending_reason
            self._pending_reason = ""
            self._timer = None
        logger.info("sync triggered (%s)", reason)
        try:
            self._sync_fn()
        except Exception:  # noqa: BLE001
            logger.exception("watch sync failed")

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def _make_pipeline(workspace: Path, artifacts: Path) -> IngestionPipeline:
    return IngestionPipeline(
        workspace_root=workspace,
        artifact_dir=artifacts,
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )


def run_poll_watcher(
    repo: Path,
    *,
    pipeline: IngestionPipeline,
    debounce_ms: int,
    poll_ms: int,
    stop_event: threading.Event,
) -> None:
    debouncer = DebouncedSync(
        lambda: _sync_once(pipeline, repo),
        debounce_ms,
    )
    prev = _snapshot(repo)
    logger.info(
        "polling watcher on %s (poll=%sms debounce=%sms)",
        repo,
        poll_ms,
        debounce_ms,
    )
    # Initial index
    _sync_once(pipeline, repo)
    try:
        while not stop_event.wait(poll_ms / 1000.0):
            cur = _snapshot(repo)
            if cur != prev:
                prev = cur
                debouncer.poke("poll-diff")
    finally:
        debouncer.cancel()


def run_watchdog_watcher(
    repo: Path,
    *,
    pipeline: IngestionPipeline,
    debounce_ms: int,
    stop_event: threading.Event,
) -> None:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    debouncer = DebouncedSync(
        lambda: _sync_once(pipeline, repo),
        debounce_ms,
    )

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: ANN001
            if getattr(event, "is_directory", False):
                return
            path = Path(getattr(event, "src_path", "") or "")
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                return
            try:
                rel_parts = path.relative_to(repo).parts
            except ValueError:
                return
            if any(part in settings.exclude_dirs for part in rel_parts):
                return
            debouncer.poke(f"fs:{getattr(event, 'event_type', 'event')}")

    observer = Observer()
    observer.schedule(Handler(), str(repo), recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("watchdog watcher on %s (debounce=%sms)", repo, debounce_ms)
    _sync_once(pipeline, repo)
    try:
        while not stop_event.wait(0.5):
            pass
    finally:
        debouncer.cancel()
        observer.stop()
        observer.join(timeout=5)


def _sync_once(pipeline: IngestionPipeline, repo: Path) -> None:
    result = pipeline.run(str(repo))
    logger.info(
        "synced repo_id=%s mode=%s changed=%s deleted=%s took_ms=%s",
        result.repo_id,
        result.graph_update_mode,
        len(result.changed_files),
        len(result.deleted_files),
        result.sync_took_ms,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.watch",
        description="Keep RepoScope graph artifacts fresh for a local repository.",
    )
    parser.add_argument("--repo", required=True, help="Local repository path to watch")
    parser.add_argument(
        "--workspace",
        default=str(settings.workspace_root),
        help="Ingestion workspace root (default: data/workspace)",
    )
    parser.add_argument(
        "--artifacts",
        default=str(settings.artifact_dir),
        help="Artifact dir (default: data/artifacts)",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "poll", "watchdog"],
        default="auto",
        help="Watcher backend (default: auto)",
    )
    parser.add_argument(
        "--debounce-ms",
        type=int,
        default=None,
        help="Debounce window in ms (default: REPOSCOPE_WATCH_DEBOUNCE_MS or 2000)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        logger.error("repo path is not a directory: %s", repo)
        return 2

    debounce = args.debounce_ms if args.debounce_ms is not None else _debounce_ms()
    pipeline = _make_pipeline(Path(args.workspace), Path(args.artifacts))
    stop = threading.Event()

    def _handle_sig(_signum, _frame):  # noqa: ANN001
        logger.info("shutting down watcher…")
        stop.set()

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    backend = args.backend
    if backend == "auto":
        try:
            import watchdog  # noqa: F401

            backend = "watchdog"
        except ImportError:
            backend = "poll"
            logger.info("watchdog not installed; using poll backend")

    if backend == "watchdog":
        try:
            run_watchdog_watcher(
                repo, pipeline=pipeline, debounce_ms=debounce, stop_event=stop
            )
        except ImportError:
            logger.warning("watchdog unavailable; falling back to poll")
            run_poll_watcher(
                repo,
                pipeline=pipeline,
                debounce_ms=debounce,
                poll_ms=_poll_ms(),
                stop_event=stop,
            )
    else:
        run_poll_watcher(
            repo,
            pipeline=pipeline,
            debounce_ms=debounce,
            poll_ms=_poll_ms(),
            stop_event=stop,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
