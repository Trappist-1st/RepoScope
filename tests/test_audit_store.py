from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.audit.store import AgentRunRecord, PostgresAgentRunStore


def test_postgres_store_uses_connection_pool():
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None
    mock_conn.execute.return_value.fetchall.return_value = []

    @contextmanager
    def _connection():
        yield mock_conn

    mock_pool = MagicMock()
    mock_pool.connection = _connection

    with patch("psycopg_pool.ConnectionPool", return_value=mock_pool) as pool_cls:
        store = PostgresAgentRunStore("postgresql://user:pass@localhost/db", min_size=2, max_size=8)
        pool_cls.assert_called_once()
        kwargs = pool_cls.call_args.kwargs
        assert kwargs["conninfo"] == "postgresql://user:pass@localhost/db"
        assert kwargs["min_size"] == 2
        assert kwargs["max_size"] == 8
        assert kwargs["open"] is True

        # Schema applied once via pooled connection (not raw connect-per-call)
        assert mock_conn.execute.call_count >= 1
        assert mock_conn.commit.call_count >= 1

        record = AgentRunRecord(
            run_id="abc",
            repo_id="r1",
            question="q",
            intent="summary",
        )
        store.save(record)
        store.get("abc")
        store.list_recent(5)
        store.close()
        mock_pool.close.assert_called_once()
