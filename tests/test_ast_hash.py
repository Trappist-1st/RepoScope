"""AST structure hashing: what counts as a structural change."""

from __future__ import annotations

from app.ingestion.ast_hash import ast_structure_hash, split_structural_changes
from app.parsing.ast_parser import AstParser

BASE = """\
class Repo:
    def find(self, key):
        return key


def use(key):
    r = Repo()
    return r.find(key)
"""

COMMENT_ONLY = """\
# added banner comment
class Repo:
    # explain the lookup
    def find(self, key):
        return key


def use(key):
    r = Repo()
    return r.find(key)
"""

NEW_METHOD = """\
class Repo:
    def find(self, key):
        return key

    def remove(self, key):
        return None


def use(key):
    r = Repo()
    return r.find(key)
"""

NEW_CALL = """\
class Repo:
    def find(self, key):
        return key


def use(key):
    r = Repo()
    print(key)
    return r.find(key)
"""


def _hash(source: str) -> str:
    defs = AstParser().parse_definitions(source, "python")
    call_names = [
        w.strip("(") for w in source.replace("(", "( ").split() if w.endswith("(")
    ]
    return ast_structure_hash(defs, call_names=call_names)


def test_comment_and_line_shift_do_not_change_hash():
    assert _hash(BASE) == _hash(COMMENT_ONLY)


def test_new_definition_changes_hash():
    assert _hash(BASE) != _hash(NEW_METHOD)


def test_new_call_changes_hash():
    assert _hash(BASE) != _hash(NEW_CALL)


def test_split_partitions_cosmetic_from_structural():
    structural, cosmetic = split_structural_changes(
        ["a.py", "b.py", "c.py"],
        previous_hashes={"a.py": "h1", "b.py": "h2"},
        current_hashes={"a.py": "h1", "b.py": "CHANGED", "c.py": "h3"},
    )
    assert cosmetic == ["a.py"]
    # c.py has no previous hash, so it cannot be proven cosmetic.
    assert structural == ["b.py", "c.py"]
