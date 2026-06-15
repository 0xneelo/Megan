"""Tiny migration runner: applies sql/schema.sql idempotently.

The schema is written with IF NOT EXISTS throughout, so applying it repeatedly is
safe. We still record a schema_version row for visibility.
"""

from __future__ import annotations

import logging
from pathlib import Path

from megan.db.pool import get_pool

log = logging.getLogger("megan.db.migrate")

SCHEMA_VERSION = 1
_SCHEMA_FILE = Path(__file__).resolve().parents[3] / "sql" / "schema.sql"


async def apply_schema() -> None:
    pool = await get_pool()
    sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                """
                INSERT INTO schema_version (version)
                VALUES ($1)
                ON CONFLICT (version) DO NOTHING
                """,
                SCHEMA_VERSION,
            )
    log.info("schema applied (version %s)", SCHEMA_VERSION)
