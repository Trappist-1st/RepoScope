from __future__ import annotations

import hashlib
from pathlib import Path


def content_hash(content: bytes | str) -> str:
    if isinstance(content, str):
        data = content.encode("utf-8", errors="replace")
    else:
        data = content
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    return content_hash(path.read_bytes())
