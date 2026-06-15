"""Content-hash dedup.

Critical given the burner-account fragility: re-running an export or re-reading
Saved Messages must never create duplicates. The hash is over normalized content,
so the same text/file ingested via different paths collapses to one inbox row.
"""

from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WS.sub(" ", text or "").strip().lower()


def content_hash(*, raw_type: str, payload: str) -> str:
    """Hash for a text-like item (text, transcript, link, extracted file text)."""
    norm = normalize_text(payload)
    return hashlib.sha256(f"{raw_type}\n{norm}".encode()).hexdigest()


def bytes_hash(raw_type: str, data: bytes) -> str:
    """Hash for binary payloads (images, files) by content."""
    h = hashlib.sha256()
    h.update(raw_type.encode())
    h.update(b"\n")
    h.update(data)
    return h.hexdigest()
