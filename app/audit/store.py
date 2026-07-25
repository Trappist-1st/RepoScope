from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


IndexingStatus = Literal["cached", "incremental", "full_reindex"]
AuditBackend = Literal["postgres", "in_memory"]


class AgentRunRecord(BaseModel):
    run_id: str
    repo_id: str
    question: str
    intent: str | None = None
    node_timings: dict[str, float] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    review_passed: bool | None = None
    low_confidence: bool = False
    status: str = "ok"
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now)


class AgentRunStore(ABC):
    backend: AuditBackend

    @abstractmethod
    def save(self, record: AgentRunRecord) -> None: ...

    @abstractmethod
    def get(self, run_id: str) -> AgentRunRecord | None: ...

    @abstractmethod
    def list_recent(self, limit: int = 20) -> list[AgentRunRecord]: ...

    def close(self) -> None:
        """Release resources (no-op for in-memory)."""

    def persistence_warning(self) -> str | None:
        if self.backend == "in_memory":
            return (
                "audit_backend: in_memory (non-persistent, will be lost on restart)"
            )
        return None


class InMemoryAgentRunStore(AgentRunStore):
    backend: AuditBackend = "in_memory"

    def __init__(self) -> None:
        self._runs: dict[str, AgentRunRecord] = {}
        self._lock = Lock()

    def save(self, record: AgentRunRecord) -> None:
        with self._lock:
            self._runs[record.run_id] = record

    def get(self, run_id: str) -> AgentRunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_recent(self, limit: int = 20) -> list[AgentRunRecord]:
        with self._lock:
            rows = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
            return rows[:limit]


def _row_to_record(row: tuple[Any, ...]) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=row[0],
        repo_id=row[1],
        question=row[2],
        intent=row[3],
        node_timings=row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
        result=row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}"),
        review_passed=row[6],
        low_confidence=bool(row[7]),
        status=row[8],
        warnings=row[9] if isinstance(row[9], list) else json.loads(row[9] or "[]"),
        created_at=row[10],
    )


class PostgresAgentRunStore(AgentRunStore):
    backend: AuditBackend = "postgres"

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        from pathlib import Path

        from psycopg_pool import ConnectionPool

        self._database_url = database_url
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            open=True,
            kwargs={"autocommit": False},
        )
        schema = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
        with self._pool.connection() as conn:
            conn.execute(schema.read_text(encoding="utf-8"))
            conn.commit()

    def close(self) -> None:
        self._pool.close()

    def save(self, record: AgentRunRecord) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                    run_id, repo_id, question, intent, node_timings, result,
                    review_passed, low_confidence, status, warnings, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s::jsonb, %s::timestamptz
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    repo_id = EXCLUDED.repo_id,
                    question = EXCLUDED.question,
                    intent = EXCLUDED.intent,
                    node_timings = EXCLUDED.node_timings,
                    result = EXCLUDED.result,
                    review_passed = EXCLUDED.review_passed,
                    low_confidence = EXCLUDED.low_confidence,
                    status = EXCLUDED.status,
                    warnings = EXCLUDED.warnings
                """,
                (
                    record.run_id,
                    record.repo_id,
                    record.question,
                    record.intent,
                    json.dumps(record.node_timings),
                    json.dumps(record.result),
                    record.review_passed,
                    record.low_confidence,
                    record.status,
                    json.dumps(record.warnings),
                    record.created_at,
                ),
            )
            conn.commit()

    def get(self, run_id: str) -> AgentRunRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                SELECT run_id, repo_id, question, intent, node_timings, result,
                       review_passed, low_confidence, status, warnings, created_at::text
                FROM agent_runs WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def list_recent(self, limit: int = 20) -> list[AgentRunRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT run_id, repo_id, question, intent, node_timings, result,
                       review_passed, low_confidence, status, warnings, created_at::text
                FROM agent_runs ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [_row_to_record(row) for row in rows]


def new_run_id() -> str:
    return uuid.uuid4().hex


def create_agent_run_store(database_url: str | None = None) -> AgentRunStore:
    if database_url:
        try:
            return PostgresAgentRunStore(database_url)
        except Exception:
            # Fall back — caller should still surface in_memory warning
            return InMemoryAgentRunStore()
    return InMemoryAgentRunStore()
