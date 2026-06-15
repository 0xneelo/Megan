"""Telegram transport — a Telethon userbot on a burner account.

The account is a TRANSPORT, not a datastore: everything it receives is persisted
to Postgres immediately. Automating a user account violates Telegram's ToS and the
burner can be banned without warning, so ingestion is idempotent (content-hash
dedup) and a periodic-export fallback is documented in the README.
"""

from megan.telegram.userbot import InboundMessage, TelegramUserbot

__all__ = ["TelegramUserbot", "InboundMessage"]
