"""The ingestion pipeline glue: ingest -> dedup -> extract -> classify -> enqueue.

A raw row is written to `inbox` immediately, before any extraction or LLM call,
so nothing is lost if a downstream service is down. Everything after that is
best-effort and can be retried.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from megan.db.repository import Repository
from megan.ingest import dedup, extract
from megan.ingest.transcribe import Transcriber
from megan.llm.client import ClaudeClient

log = logging.getLogger("megan.ingest")

_IMAGE_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass
class RawItem:
    """A single thing to ingest, from any source."""

    source: str  # dm | forward | saved_sweep | upload
    raw_type: str  # text | voice | image | link | file
    text: str | None = None
    file_path: str | None = None
    raw_ref: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class IngestPipeline:
    def __init__(
        self, repo: Repository, claude: ClaudeClient, transcriber: Transcriber
    ) -> None:
        self.repo = repo
        self.claude = claude
        self.transcriber = transcriber

    async def ingest(self, item: RawItem) -> dict[str, Any] | None:
        """Run the full pipeline for one item. Returns the enqueued inbox row, or
        None if it was a dedup hit (already ingested)."""
        chash = self._hash(item)
        row = await self.repo.insert_inbox(
            source=item.source,
            raw_type=item.raw_type,
            content_hash=chash,
            raw_ref=item.raw_ref,
            meta=item.meta,
        )
        if row is None:
            log.info("dedup hit (%s) — skipping", item.raw_type)
            return None

        inbox_id = row["id"]
        extracted, extra_meta = await self._extract(item)
        if extra_meta:
            # merge any extraction metadata (image kind/intent, link url, ...)
            merged = {**(item.meta or {}), **extra_meta}
            row["meta"] = merged

        extracted = (extracted or "").strip()
        if not extracted:
            # Extraction yielded nothing (empty transcript, dead link, unreadable
            # file). Park it as needs_attention so it isn't invisibly stuck pending
            # — next_pending_item requires extracted_text, so it'd never surface.
            log.warning("inbox %s extracted to empty (%s); needs attention", inbox_id, item.raw_type)
            await self.repo.set_inbox_status(inbox_id, "needs_attention")
            row["status"] = "needs_attention"
            return row

        await self.repo.set_inbox_extracted(inbox_id, extracted)
        row["extracted_text"] = extracted

        # Cheap first-pass classification.
        try:
            kind = await self.claude.classify(extracted)
        except Exception as exc:  # noqa: BLE001
            log.warning("classify failed for inbox %s: %s", inbox_id, exc)
            kind = "ambiguous"
        await self.repo.set_inbox_classify(inbox_id, kind)
        row["classify_type"] = kind

        return row

    # -------------------------------------------------------- hashing
    def _hash(self, item: RawItem) -> str:
        if item.raw_type in ("text", "link"):
            return dedup.content_hash(raw_type=item.raw_type, payload=item.text or "")
        if item.file_path and Path(item.file_path).exists():
            data = Path(item.file_path).read_bytes()
            return dedup.bytes_hash(item.raw_type, data)
        # Fallback: hash whatever ref/text we have.
        return dedup.content_hash(
            raw_type=item.raw_type, payload=(item.text or item.raw_ref or "")
        )

    # ------------------------------------------------------- extraction
    async def _extract(self, item: RawItem) -> tuple[str, dict[str, Any]]:
        if item.raw_type == "text":
            text = item.text or ""
            url = extract.find_first_url(text)
            if url:
                fetched = await extract.fetch_link(url)
                if fetched["text"]:
                    return (
                        f"{text}\n\n[link: {fetched['title']}]\n{fetched['text']}",
                        {"link_url": url, "link_title": fetched["title"]},
                    )
            return text, {}

        if item.raw_type == "link":
            url = item.text or extract.find_first_url(item.text or "") or ""
            fetched = await extract.fetch_link(url)
            return (
                f"[link: {fetched['title']}]\n{fetched['text']}",
                {"link_url": url, "link_title": fetched["title"]},
            )

        if item.raw_type == "voice":
            if not item.file_path:
                return "", {}
            transcript = await self.transcriber.transcribe(item.file_path)
            return transcript, {"transcribed": bool(transcript)}

        if item.raw_type == "image":
            return await self._extract_image(item)

        if item.raw_type == "file":
            if not item.file_path:
                return "", {}
            return extract.extract_file(item.file_path), {
                "filename": Path(item.file_path).name
            }

        return item.text or "", {}

    async def _extract_image(self, item: RawItem) -> tuple[str, dict[str, Any]]:
        if not item.file_path or not Path(item.file_path).exists():
            return "", {}
        suffix = Path(item.file_path).suffix.lower()
        media_type = _IMAGE_MEDIA.get(suffix, "image/png")
        data = Path(item.file_path).read_bytes()
        b64 = base64.standard_b64encode(data).decode("ascii")
        try:
            result = await self.claude.read_image(b64, media_type=media_type)
        except Exception as exc:  # noqa: BLE001
            log.warning("vision failed: %s", exc)
            return "", {}
        text = result.get("text", "")
        kind = result.get("kind", "")
        intent = result.get("intent", "")
        combined = f"[screenshot: {kind}]\n{text}"
        if intent:
            combined += f"\n\n(likely intent: {intent})"
        return combined, {"image_kind": kind, "image_intent": intent}
