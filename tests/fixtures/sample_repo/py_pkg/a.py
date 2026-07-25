"""Helpers used by b.py."""


def greet(name: str) -> str:
    return f"hello, {name}"


class Helper:
    def name(self) -> str:
        return "reposcope"

    def shout(self) -> str:
        return greet(self.name()).upper()
