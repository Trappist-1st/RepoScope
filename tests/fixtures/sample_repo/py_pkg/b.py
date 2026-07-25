"""Sample Python package for ingestion tests."""

from py_pkg.a import greet, Helper


def run() -> str:
    helper = Helper()
    return greet(helper.name())
