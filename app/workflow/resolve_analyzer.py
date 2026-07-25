"""Select Analyzer implementation from settings / explicit override."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.workflow.analyzers import StubAnalyzer
from app.workflow.llm_analyzer import LLMAnalyzer
from app.workflow.llm_client import is_placeholder_api_key

if TYPE_CHECKING:
    from app.workflow.analyzers import Analyzer


def resolve_analyzer(analyzer: Analyzer | None = None) -> Analyzer:
    """
    Prefer an explicit analyzer. Otherwise use REPOSCOPE_ANALYZER_PROVIDER.
    provider=llm with a missing/placeholder key falls back to StubAnalyzer so
    tests and first-time setup still run without a live API key.
    """
    if analyzer is not None:
        return analyzer

    from app.config import settings

    provider = (settings.analyzer_provider or "stub").strip().lower()
    if provider in {"", "stub", "none", "rule"}:
        return StubAnalyzer()

    if provider in {"llm", "openai", "openai_compatible"}:
        key = settings.llm_api_key
        if is_placeholder_api_key(key):
            return StubAnalyzer()
        return LLMAnalyzer(
            api_key=key or "",
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_sec=settings.llm_timeout_sec,
            json_response=settings.llm_json_response,
        )

    raise ValueError(
        f"Unknown REPOSCOPE_ANALYZER_PROVIDER={provider!r}; use 'stub' or 'llm'"
    )
