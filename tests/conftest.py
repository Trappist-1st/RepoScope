from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"


@pytest.fixture(autouse=True)
def _force_stub_analyzer_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Keep unit/integration tests offline: do not call a live LLM for planner/analyzer
    even if the developer's .env points at a real provider/key.
    """
    monkeypatch.setattr("app.config.settings.analyzer_provider", "stub")
    monkeypatch.setattr("app.config.settings.llm_api_key", "YOUR_LLM_API_KEY")
