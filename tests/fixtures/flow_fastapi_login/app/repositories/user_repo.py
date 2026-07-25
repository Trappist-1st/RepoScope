"""User persistence gateway."""


def find_by_username(username: str) -> str | None:
    if not username:
        return None
    return username
