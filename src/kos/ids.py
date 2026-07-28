from __future__ import annotations

import hashlib


def stable_id(prefix: str, *parts: object, length: int = 12) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"
