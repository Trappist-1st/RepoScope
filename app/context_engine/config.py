from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.config import settings


class WeightConfig(BaseModel):
    entry: float = 0.20
    graph: float = 0.30
    relevance: float = 0.40
    tier: float = 0.10


class GraphMixConfig(BaseModel):
    file: float = 0.5
    symbol: float = 0.5


class BucketConfig(BaseModel):
    code: float = 0.70
    graph: float = 0.15
    history: float = 0.10
    reserve: float = 0.05


class ContextConfig(BaseModel):
    token_budget: int = 4000
    weights: WeightConfig = Field(default_factory=WeightConfig)
    graph_mix: GraphMixConfig = Field(default_factory=GraphMixConfig)
    buckets: BucketConfig = Field(default_factory=BucketConfig)
    history_window: int = 1
    entry_files: list[str] = Field(
        default_factory=lambda: [
            "main.py",
            "app.py",
            "__main__.py",
            "manage.py",
            "wsgi.py",
            "asgi.py",
            "index.js",
            "index.ts",
            "index.tsx",
            "main.ts",
            "main.js",
            "app.ts",
            "Application.java",
            "Main.java",
            "App.java",
            "Bootstrap.java",
        ]
    )


def _default_path() -> Path:
    if settings.context_config_path is not None:
        return Path(settings.context_config_path)
    return Path(__file__).resolve().parents[2] / "config" / "context.yaml"


def load_context_config(path: Path | None = None) -> ContextConfig:
    cfg_path = path or _default_path()
    data: dict[str, Any] = {}
    if cfg_path.exists():
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Invalid context config: {cfg_path}")
        data = loaded
    return ContextConfig.model_validate(data)
