from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from app.models.schemas import FileIndexRecord


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FilesRepository(ABC):
    @abstractmethod
    def upsert_file(self, record: FileIndexRecord) -> None: ...

    @abstractmethod
    def get_file_hashes(self, repo_id: str) -> dict[str, str]: ...

    @abstractmethod
    def delete_files(self, repo_id: str, file_paths: list[str]) -> None: ...

    @abstractmethod
    def list_files(self, repo_id: str) -> list[FileIndexRecord]: ...


class ReposRepository(ABC):
    @abstractmethod
    def upsert_repo(self, repo_id: str, source: str, commit_hash: str, local_path: str) -> None: ...

    @abstractmethod
    def get_repo(self, repo_id: str) -> dict | None: ...


class InMemoryFilesRepository(FilesRepository):
    def __init__(self) -> None:
        self._files: dict[tuple[str, str], FileIndexRecord] = {}
        self._lock = Lock()

    def upsert_file(self, record: FileIndexRecord) -> None:
        with self._lock:
            self._files[(record.repo_id, record.file_path)] = record

    def get_file_hashes(self, repo_id: str) -> dict[str, str]:
        with self._lock:
            return {
                path: rec.content_hash
                for (rid, path), rec in self._files.items()
                if rid == repo_id
            }

    def delete_files(self, repo_id: str, file_paths: list[str]) -> None:
        with self._lock:
            for path in file_paths:
                self._files.pop((repo_id, path), None)

    def list_files(self, repo_id: str) -> list[FileIndexRecord]:
        with self._lock:
            return [rec for (rid, _), rec in self._files.items() if rid == repo_id]


class InMemoryReposRepository(ReposRepository):
    def __init__(self) -> None:
        self._repos: dict[str, dict] = {}
        self._lock = Lock()

    def upsert_repo(self, repo_id: str, source: str, commit_hash: str, local_path: str) -> None:
        with self._lock:
            self._repos[repo_id] = {
                "repo_id": repo_id,
                "source": source,
                "commit_hash": commit_hash,
                "local_path": local_path,
                "updated_at": _utc_now_iso(),
            }

    def get_repo(self, repo_id: str) -> dict | None:
        with self._lock:
            return self._repos.get(repo_id)


class PostgresFilesRepository(FilesRepository):
    def __init__(self, database_url: str) -> None:
        import psycopg

        self._database_url = database_url
        self._psycopg = psycopg
        self._ensure_schema()

    def _connect(self):
        return self._psycopg.connect(self._database_url)

    def _ensure_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        sql = schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.execute(sql)
            conn.commit()

    def upsert_file(self, record: FileIndexRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files (repo_id, file_path, content_hash, last_indexed_at)
                VALUES (%s, %s, %s, %s::timestamptz)
                ON CONFLICT (repo_id, file_path) DO UPDATE
                SET content_hash = EXCLUDED.content_hash,
                    last_indexed_at = EXCLUDED.last_indexed_at
                """,
                (record.repo_id, record.file_path, record.content_hash, record.last_indexed_at),
            )
            conn.commit()

    def get_file_hashes(self, repo_id: str) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_path, content_hash FROM files WHERE repo_id = %s",
                (repo_id,),
            ).fetchall()
        return {path: digest for path, digest in rows}

    def delete_files(self, repo_id: str, file_paths: list[str]) -> None:
        if not file_paths:
            return
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM files WHERE repo_id = %s AND file_path = ANY(%s)",
                (repo_id, file_paths),
            )
            conn.commit()

    def list_files(self, repo_id: str) -> list[FileIndexRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT repo_id, file_path, content_hash, last_indexed_at::text
                FROM files WHERE repo_id = %s
                """,
                (repo_id,),
            ).fetchall()
        return [
            FileIndexRecord(
                repo_id=r[0],
                file_path=r[1],
                content_hash=r[2],
                last_indexed_at=r[3],
            )
            for r in rows
        ]


class PostgresReposRepository(ReposRepository):
    def __init__(self, database_url: str) -> None:
        import psycopg

        self._database_url = database_url
        self._psycopg = psycopg
        self._ensure_schema()

    def _connect(self):
        return self._psycopg.connect(self._database_url)

    def _ensure_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        sql = schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.execute(sql)
            conn.commit()

    def upsert_repo(self, repo_id: str, source: str, commit_hash: str, local_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repos (repo_id, source, commit_hash, local_path, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (repo_id) DO UPDATE
                SET source = EXCLUDED.source,
                    commit_hash = EXCLUDED.commit_hash,
                    local_path = EXCLUDED.local_path,
                    updated_at = NOW()
                """,
                (repo_id, source, commit_hash, local_path),
            )
            conn.commit()

    def get_repo(self, repo_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT repo_id, source, commit_hash, local_path, updated_at::text
                FROM repos WHERE repo_id = %s
                """,
                (repo_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "repo_id": row[0],
            "source": row[1],
            "commit_hash": row[2],
            "local_path": row[3],
            "updated_at": row[4],
        }


def create_repositories(
    database_url: str | None = None,
) -> tuple[FilesRepository, ReposRepository]:
    if database_url:
        return PostgresFilesRepository(database_url), PostgresReposRepository(database_url)
    return InMemoryFilesRepository(), InMemoryReposRepository()
