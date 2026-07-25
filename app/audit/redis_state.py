from __future__ import annotations

import json
from abc import ABC, abstractmethod
from threading import Lock
from typing import Any


class RunStateCache(ABC):
    @abstractmethod
    def set(self, run_id: str, state: dict[str, Any], ttl_seconds: int = 3600) -> None: ...

    @abstractmethod
    def get(self, run_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def delete(self, run_id: str) -> None: ...

    @property
    @abstractmethod
    def backend(self) -> str: ...


class InMemoryRunStateCache(RunStateCache):
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    @property
    def backend(self) -> str:
        return "in_memory"

    def set(self, run_id: str, state: dict[str, Any], ttl_seconds: int = 3600) -> None:
        with self._lock:
            self._data[run_id] = dict(state)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            val = self._data.get(run_id)
            return dict(val) if val is not None else None

    def delete(self, run_id: str) -> None:
        with self._lock:
            self._data.pop(run_id, None)


class RedisRunStateCache(RunStateCache):
    def __init__(self, redis_url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    @property
    def backend(self) -> str:
        return "redis"

    def _key(self, run_id: str) -> str:
        return f"reposcope:run:{run_id}"

    def set(self, run_id: str, state: dict[str, Any], ttl_seconds: int = 3600) -> None:
        self._client.setex(self._key(run_id), ttl_seconds, json.dumps(state))

    def get(self, run_id: str) -> dict[str, Any] | None:
        raw = self._client.get(self._key(run_id))
        if raw is None:
            return None
        return json.loads(raw)

    def delete(self, run_id: str) -> None:
        self._client.delete(self._key(run_id))


def create_run_state_cache(redis_url: str | None = None) -> RunStateCache:
    if redis_url:
        try:
            cache = RedisRunStateCache(redis_url)
            # ping
            cache.set("__ping__", {"ok": True}, ttl_seconds=5)
            cache.delete("__ping__")
            return cache
        except Exception:
            return InMemoryRunStateCache()
    return InMemoryRunStateCache()
