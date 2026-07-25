from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_TIMEOUTS: dict[str, float] = {
    "route": 5.0,
    "repo_parse": 120.0,
    "planner": 60.0,
    "retrieve": 30.0,
    "analyze": 90.0,
    "review": 10.0,
    "finalize": 10.0,
}


class NodeTimeoutError(TimeoutError):
    def __init__(self, node: str, seconds: float) -> None:
        self.node = node
        self.seconds = seconds
        super().__init__(f"node `{node}` timed out after {seconds}s")


def run_with_timeout(node: str, fn: Callable[[], T], timeout: float | None = None) -> T:
    seconds = DEFAULT_TIMEOUTS.get(node, 30.0) if timeout is None else timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=seconds)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise NodeTimeoutError(node, seconds) from exc
