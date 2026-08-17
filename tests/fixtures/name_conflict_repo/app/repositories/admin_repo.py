"""Admin persistence gateway.

Defines a module-level ``find_by_username`` with the same simple name as the
one in ``user_repo``, so a resolver that only matches on the bare name has two
equally plausible targets.
"""


def find_by_username(username: str) -> str | None:
    if not username:
        return None
    return f"admin:{username}"
