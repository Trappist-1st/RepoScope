"""Auth application service."""

from app.repositories.user_repo import find_by_username


def login(username: str, password: str) -> str:
    user = find_by_username(username)
    if user is None:
        return "unauthorized"
    return f"token-for-{user}"
