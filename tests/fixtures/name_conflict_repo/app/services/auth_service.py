"""Auth service that exercises the receiver-shadowing case.

This module defines a *local* helper called ``find_by_username`` and also calls
``repo.find_by_username(...)`` on a UserRepo instance. A resolver that checks
same-file top-level symbols before looking at the receiver will wrongly bind the
qualified call to the local helper.
"""

from app.repositories.user_repo import UserRepo


def find_by_username(username: str) -> str | None:
    """Local cache helper - deliberately shares the repository method name."""
    return None


def login(username: str, password: str) -> str:
    repo = UserRepo()
    user = repo.find_by_username(username)
    if user is None:
        return "unauthorized"
    return f"token-for-{user}"
