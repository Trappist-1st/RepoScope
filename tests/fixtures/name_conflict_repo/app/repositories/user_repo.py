"""User persistence gateway."""


class UserRepo:
    def find_by_username(self, username: str) -> str | None:
        if not username:
            return None
        return username
