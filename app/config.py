from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPOSCOPE_", env_file=".env", extra="ignore")

    workspace_root: Path = Path("data/workspace")
    artifact_dir: Path = Path("data/artifacts")
    database_url: str | None = None  # e.g. postgresql://reposcope:reposcope@localhost:5432/reposcope
    fallback_chunk_lines: int = 80
    exclude_dirs: frozenset[str] = frozenset(
        {
            ".git",
            "node_modules",
            ".venv",
            "venv",
            "__pycache__",
            "dist",
            "build",
            ".idea",
            ".vscode",
            "target",
            ".next",
            "coverage",
        }
    )

    # Retrieval (phase 2)
    retrieval_config_path: Path | None = None
    vector_backend: str | None = None  # inmemory | qdrant
    qdrant_url: str | None = None
    rerank_enabled: bool | None = None

    # Context engineering (phase 4)
    context_config_path: Path | None = None

    # Observability (phase 5)
    redis_url: str | None = None  # e.g. redis://localhost:6379/0
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Analyzer / LLM (OpenAI-compatible chat API)
    # stub = rule-based StubAnalyzer; llm = LLMAnalyzer via base_url/model/api_key
    analyzer_provider: str = "llm"
    llm_api_key: str | None = "YOUR_LLM_API_KEY"
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_sec: float = 90.0
    llm_json_response: bool = True


settings = Settings()
