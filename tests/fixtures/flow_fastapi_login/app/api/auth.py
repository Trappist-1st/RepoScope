"""FastAPI-style login route (fixture for Flow Trace)."""

from app.services.auth_service import login as auth_login


def login(username: str, password: str) -> str:
    """HTTP login handler."""
    return auth_login(username, password)


def health() -> str:
    return "ok"
