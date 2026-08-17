"""Admin service: unqualified call that only the import map can disambiguate.

``find_by_username`` exists in both repository modules, so the bare call below
is ambiguous unless the resolver reads the import statement above it.
"""

from app.repositories.admin_repo import find_by_username


def promote(username: str) -> str:
    found = find_by_username(username)
    return found or "missing"
