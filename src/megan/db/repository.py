"""Repository — all SQL lives here. The rest of the app speaks dicts.

Postgres is the source of truth. Methods are deliberately small and explicit so
the data model stays legible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg

from megan.db.pool import get_pool


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(record) if record is not None else None


def _rows(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in records]


class Repository:
    """Thin async data-access layer over asyncpg."""

    # ------------------------------------------------------------------ inbox
    async def insert_inbox(
        self,
        *,
        source: str,
        raw_type: str,
        content_hash: str,
        raw_ref: str | None = None,
        extracted_text: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Insert a raw inbox row. Returns the row, or None if it's a dedup hit.

        This is called BEFORE any LLM/downstream work, so nothing is lost if a
        later step fails. Dedup is enforced by the content_hash unique index.
        """
        pool = await get_pool()
        rec = await pool.fetchrow(
            """
            INSERT INTO inbox (source, raw_type, content_hash, raw_ref, extracted_text, meta)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (content_hash) DO NOTHING
            RETURNING *
            """,
            source,
            raw_type,
            content_hash,
            raw_ref,
            extracted_text,
            meta or {},
        )
        return _row(rec)

    async def get_inbox(self, inbox_id: int) -> dict[str, Any] | None:
        pool = await get_pool()
        return _row(await pool.fetchrow("SELECT * FROM inbox WHERE id = $1", inbox_id))

    async def set_inbox_extracted(self, inbox_id: int, extracted_text: str) -> None:
        pool = await get_pool()
        await pool.execute(
            "UPDATE inbox SET extracted_text = $2 WHERE id = $1", inbox_id, extracted_text
        )

    async def set_inbox_classify(self, inbox_id: int, classify_type: str) -> None:
        pool = await get_pool()
        await pool.execute(
            "UPDATE inbox SET classify_type = $2 WHERE id = $1", inbox_id, classify_type
        )

    async def set_inbox_status(
        self, inbox_id: int, status: str, routed_to: str | None = None
    ) -> None:
        pool = await get_pool()
        await pool.execute(
            """
            UPDATE inbox
            SET status = $2,
                routed_to = COALESCE($3, routed_to),
                processed_at = CASE WHEN $2 IN ('routed', 'dropped') THEN now() ELSE processed_at END
            WHERE id = $1
            """,
            inbox_id,
            status,
            routed_to,
        )

    async def next_pending_item(self) -> dict[str, Any] | None:
        """Oldest still-pending item that has extracted text and isn't being asked about."""
        pool = await get_pool()
        return _row(
            await pool.fetchrow(
                """
                SELECT * FROM inbox
                WHERE status = 'pending'
                  AND extracted_text IS NOT NULL
                ORDER BY created_at ASC
                LIMIT 1
                """
            )
        )

    async def count_pending(self) -> int:
        pool = await get_pool()
        return int(await pool.fetchval("SELECT count(*) FROM inbox WHERE status = 'pending'"))

    async def tasks_routed_today(self) -> int:
        pool = await get_pool()
        return int(
            await pool.fetchval(
                """
                SELECT count(*) FROM inbox
                WHERE routed_to LIKE 'linear:%'
                  AND processed_at >= date_trunc('day', now())
                """
            )
        )

    # ------------------------------------------------------------- open_asks
    async def count_open_asks(self) -> int:
        pool = await get_pool()
        return int(
            await pool.fetchval("SELECT count(*) FROM open_asks WHERE answered_at IS NULL")
        )

    async def has_free_ask_slot(self, max_open: int) -> bool:
        return (await self.count_open_asks()) < max_open

    async def create_open_ask(
        self,
        *,
        question: str,
        inbox_id: int | None = None,
        kind: str = "triage",
        suggested_answers: list[str] | None = None,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pool = await get_pool()
        rec = await pool.fetchrow(
            """
            INSERT INTO open_asks (inbox_id, kind, question, suggested_answers, state)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
            RETURNING *
            """,
            inbox_id,
            kind,
            question,
            suggested_answers or [],
            state or {},
        )
        if inbox_id is not None:
            await self.set_inbox_status(inbox_id, "asking")
        return _row(rec)  # type: ignore[return-value]

    async def oldest_unanswered_ask(self) -> dict[str, Any] | None:
        pool = await get_pool()
        return _row(
            await pool.fetchrow(
                """
                SELECT * FROM open_asks
                WHERE answered_at IS NULL
                ORDER BY asked_at ASC
                LIMIT 1
                """
            )
        )

    async def list_unanswered_asks(self) -> list[dict[str, Any]]:
        pool = await get_pool()
        return _rows(
            await pool.fetch(
                "SELECT * FROM open_asks WHERE answered_at IS NULL ORDER BY asked_at ASC"
            )
        )

    async def update_ask_state(self, ask_id: int, state: dict[str, Any]) -> None:
        pool = await get_pool()
        await pool.execute(
            "UPDATE open_asks SET state = $2::jsonb WHERE id = $1", ask_id, state
        )

    async def update_ask_question(
        self, ask_id: int, question: str, suggested_answers: list[str]
    ) -> None:
        pool = await get_pool()
        await pool.execute(
            "UPDATE open_asks SET question = $2, suggested_answers = $3::jsonb WHERE id = $1",
            ask_id,
            question,
            suggested_answers,
        )

    async def answer_ask(self, ask_id: int) -> None:
        pool = await get_pool()
        await pool.execute(
            "UPDATE open_asks SET answered_at = now() WHERE id = $1", ask_id
        )

    # ------------------------------------------------------------ read_later
    async def add_read_later(
        self,
        *,
        inbox_id: int | None,
        url: str | None,
        title: str | None,
        note: str | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        pool = await get_pool()
        rec = await pool.fetchrow(
            """
            INSERT INTO read_later (inbox_id, url, title, note, topic)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            inbox_id,
            url,
            title,
            note,
            topic,
        )
        return _row(rec)  # type: ignore[return-value]

    async def count_read_later_undigested(self) -> int:
        pool = await get_pool()
        return int(
            await pool.fetchval("SELECT count(*) FROM read_later WHERE digested = false")
        )

    async def top_read_later(self, limit: int = 5) -> list[dict[str, Any]]:
        pool = await get_pool()
        return _rows(
            await pool.fetch(
                """
                SELECT * FROM read_later
                WHERE digested = false
                ORDER BY created_at ASC
                LIMIT $1
                """,
                limit,
            )
        )

    # ----------------------------------------------------------------- hosts
    async def get_host(self, name: str) -> dict[str, Any] | None:
        pool = await get_pool()
        return _row(await pool.fetchrow("SELECT * FROM hosts WHERE name = $1", name))

    async def list_hosts(self) -> list[dict[str, Any]]:
        pool = await get_pool()
        return _rows(await pool.fetch("SELECT * FROM hosts ORDER BY name"))

    async def upsert_host(
        self,
        *,
        name: str,
        ssh_alias: str,
        allowed_commands: list[str],
        notes: str | None = None,
    ) -> dict[str, Any]:
        # is_production is hard-pinned to false; the CHECK constraint backs this up.
        pool = await get_pool()
        rec = await pool.fetchrow(
            """
            INSERT INTO hosts (name, ssh_alias, allowed_commands, is_production, notes)
            VALUES ($1, $2, $3::jsonb, false, $4)
            ON CONFLICT (name) DO UPDATE
              SET ssh_alias = EXCLUDED.ssh_alias,
                  allowed_commands = EXCLUDED.allowed_commands,
                  notes = EXCLUDED.notes
            RETURNING *
            """,
            name,
            ssh_alias,
            allowed_commands,
            notes,
        )
        return _row(rec)  # type: ignore[return-value]

    # -------------------------------------------------------- routing_memory
    async def add_routing_memory(
        self,
        *,
        item_summary: str,
        decision: str,
        project: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO routing_memory (item_summary, decision, project, detail)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            item_summary,
            decision,
            project,
            detail or {},
        )

    async def recent_routing_memory(self, limit: int = 20) -> list[dict[str, Any]]:
        pool = await get_pool()
        return _rows(
            await pool.fetch(
                "SELECT * FROM routing_memory ORDER BY created_at DESC LIMIT $1", limit
            )
        )

    # -------------------------------------------------------------------- kv
    async def kv_get(self, key: str) -> Any | None:
        pool = await get_pool()
        return await pool.fetchval("SELECT value FROM kv WHERE key = $1", key)

    async def kv_set(self, key: str, value: Any) -> None:
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO kv (key, value, updated_at)
            VALUES ($1, $2::jsonb, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            key,
            value,
        )

    # ------------------------------------------------------------- llm_usage
    async def record_usage(
        self,
        *,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO llm_usage (model, purpose, input_tokens, output_tokens, cost_usd)
            VALUES ($1, $2, $3, $4, $5)
            """,
            model,
            purpose,
            input_tokens,
            output_tokens,
            cost_usd,
        )

    async def month_cost_usd(self) -> float:
        pool = await get_pool()
        val = await pool.fetchval(
            """
            SELECT COALESCE(sum(cost_usd), 0) FROM llm_usage
            WHERE created_at >= date_trunc('month', now())
            """
        )
        return float(val or 0.0)


def utcnow() -> datetime:
    return datetime.now(UTC)
