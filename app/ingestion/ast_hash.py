"""Line-independent fingerprint of a file's AST structure.

The pipeline already hashes file bytes to decide what to re-parse. That signal
is too coarse for the *graph*: adding a comment shifts every line below it, so
the byte hash changes even though no symbol, base class, or call site did.

This hash covers only what the dependency graph is built from -- definition
names, kinds, owners, base types, and the set of called names -- and
deliberately excludes line numbers. When it is unchanged, the previous edges
are still correct and only node spans need refreshing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.models.schemas import Definition


def _definition_signature(d: Definition) -> str:
    bases = ",".join(sorted(f"{b.relation}:{b.name}" for b in d.bases))
    return f"{d.kind.value}|{d.parent_name or ''}|{d.name}|{bases}"


def ast_structure_hash(
    definitions: list[Definition],
    call_names: Iterable[str] | None = None,
    import_targets: Iterable[str] | None = None,
) -> str:
    """Stable digest of the structural facts that produce graph edges."""
    parts = sorted(_definition_signature(d) for d in definitions)
    parts.append("\x00calls\x00")
    parts.extend(sorted(set(call_names or ())))
    parts.append("\x00imports\x00")
    parts.extend(sorted(set(import_targets or ())))
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def split_structural_changes(
    changed: list[str],
    previous_hashes: dict[str, str],
    current_hashes: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Partition changed files into (structural, cosmetic).

    A file is cosmetic when its bytes moved but its structure hash did not.
    Files with no recorded previous hash count as structural: we cannot prove
    otherwise, and over-rebuilding is the safe direction.
    """
    structural: list[str] = []
    cosmetic: list[str] = []
    for path in changed:
        prev = previous_hashes.get(path)
        curr = current_hashes.get(path)
        if prev is not None and curr is not None and prev == curr:
            cosmetic.append(path)
        else:
            structural.append(path)
    return structural, cosmetic
