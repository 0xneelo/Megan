"""Telethon userbot client.

Conservative polling, human-like pacing on sends, and immediate persistence
upstream of this module keep the burner account as low-risk as a ToS-violating
userbot can be. If it dies, the documented fallback is periodic-export mode.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from megan.config import Settings

log = logging.getLogger("megan.telegram")

InboundHandler = Callable[["InboundMessage"], Awaitable[None]]


@dataclass
class InboundMessage:
    """A normalized inbound Telegram message, after any media is downloaded."""

    source: str  # dm | forward | saved_sweep
    raw_type: str  # text | voice | image | file
    text: str | None = None
    file_path: str | None = None
    raw_ref: str | None = None  # "chat_id:msg_id"
    is_answerable: bool = False  # text/voice can answer an open question
    meta: dict[str, Any] = field(default_factory=dict)


class TelegramUserbot:
    def __init__(self, settings: Settings, on_message: InboundHandler) -> None:
        self.settings = settings
        self.on_message = on_message
        self._client: TelegramClient | None = None
        self._owner_entity: Any = settings.owner_telegram_id
        Path(settings.download_dir).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ lifecycle
    def _build_client(self) -> TelegramClient:
        if self.settings.telegram_string_session:
            session: Any = StringSession(self.settings.telegram_string_session)
        else:
            session = self.settings.telegram_session_name
        return TelegramClient(
            session,
            int(self.settings.telegram_api_id or 0),
            self.settings.telegram_api_hash or "",
        )

    async def start(self) -> None:
        self._client = self._build_client()
        await self._client.start(phone=self.settings.telegram_phone)  # type: ignore[arg-type]
        me = await self._client.get_me()
        log.info("userbot signed in as %s (id=%s)", getattr(me, "username", "?"), me.id)
        self._client.add_event_handler(
            self._handle_new_message, events.NewMessage(incoming=True)
        )

    async def run_forever(self) -> None:
        assert self._client is not None
        await self._client.run_until_disconnected()

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()

    # -------------------------------------------------------------- sending
    async def send(self, text: str) -> None:
        if self._client is None:
            log.warning("send before client started")
            return
        target = self._owner_entity
        if target is None:
            log.warning("no owner entity known yet; cannot send")
            return
        # Human-like pacing: a short, jittered pause before replying.
        await asyncio.sleep(random.uniform(0.6, 1.8))
        await self._client.send_message(target, text)

    # ------------------------------------------------------------- handlers
    async def _handle_new_message(self, event: events.NewMessage.Event) -> None:
        sender_id = event.sender_id
        # Megan only talks to the owner. If no owner is configured (dev), latch
        # onto the first person who DMs and answer there.
        if self.settings.owner_telegram_id:
            if sender_id != self.settings.owner_telegram_id:
                return
        elif self._owner_entity is None:
            self._owner_entity = sender_id
        if self.settings.owner_telegram_id and self._owner_entity is None:
            self._owner_entity = self.settings.owner_telegram_id

        try:
            inbound = await self._normalize(event.message, source_default="dm")
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to normalize message: %s", exc)
            return
        if inbound is not None:
            await self.on_message(inbound)

    async def _normalize(self, message: Any, source_default: str) -> InboundMessage | None:
        is_forward = getattr(message, "fwd_from", None) is not None
        source = "forward" if is_forward else source_default
        raw_ref = f"{message.chat_id}:{message.id}"
        text = message.message or None

        # Photos / image documents -> vision.
        if message.photo:
            path = await self._download(message)
            return InboundMessage(source, "image", text=text, file_path=path, raw_ref=raw_ref)

        if message.voice or message.audio:
            path = await self._download(message)
            return InboundMessage(
                source, "voice", text=text, file_path=path, raw_ref=raw_ref, is_answerable=True
            )

        if message.document:
            mime = getattr(message.document, "mime_type", "") or ""
            path = await self._download(message)
            if mime.startswith("image/"):
                return InboundMessage(source, "image", text=text, file_path=path, raw_ref=raw_ref)
            return InboundMessage(source, "file", text=text, file_path=path, raw_ref=raw_ref)

        if text:
            return InboundMessage(
                source, "text", text=text, raw_ref=raw_ref, is_answerable=True
            )
        return None

    async def _download(self, message: Any) -> str | None:
        assert self._client is not None
        try:
            return await message.download_media(file=self.settings.download_dir)
        except Exception as exc:  # noqa: BLE001
            log.warning("media download failed: %s", exc)
            return None

    # -------------------------------------------------- saved-messages sweep
    async def fetch_saved_since(
        self, min_id: int
    ) -> tuple[list[InboundMessage], int]:
        """Read the burner's Saved Messages newer than min_id (idempotent sweep).

        Returns (messages, new_max_id). Forward your real Saved Messages to the
        burner to get them swept too.
        """
        if self._client is None:
            return [], min_id
        items: list[InboundMessage] = []
        max_id = min_id
        async for message in self._client.iter_messages(
            "me", min_id=min_id, reverse=True, limit=200
        ):
            max_id = max(max_id, message.id)
            inbound = await self._normalize(message, source_default="saved_sweep")
            if inbound is not None:
                inbound.source = "saved_sweep"
                inbound.is_answerable = False
                items.append(inbound)
        return items, max_id
