"""Postgres-backed integration test for the triage state machine.

Exercises the actual repository + engine against a real database, so the bug
fixes (the <=4 guard, failed-route recovery, ambiguous parking, needs-attention
for empty extractions) are verified end to end rather than via fakes alone.

Runs only when DATABASE_URL points at a reachable Postgres; otherwise it skips.
A throwaway cluster is the easiest way to run it locally:

    eval "$(pg_ctlcluster ...)"   # or see scripts/run_integration_tests.sh
    DATABASE_URL=postgresql://localhost/megan_test pytest tests/test_triage_state_machine.py
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from megan.config import get_settings
from megan.db.migrate import apply_schema
from megan.db.pool import close_pool, get_pool
from megan.db.repository import Repository
from megan.ingest.pipeline import IngestPipeline, RawItem
from megan.llm import tools as T
from megan.triage.engine import TriageEngine

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------- fakes
class FakeClaude:
    """Scripted triage decisions + trivial classify/note-writing."""

    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = list(decisions)
        self.classify_result = "note"

    async def triage_step(self, **_: Any) -> dict[str, Any]:
        return self._decisions.pop(0)

    async def write_note(self, *_: Any, **__: Any) -> str:
        return "# note\n\nbody"

    async def classify(self, _text: str) -> str:
        return self.classify_result

    async def read_image(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"text": "", "kind": "unknown"}


class FakeLinear:
    def __init__(self, raise_on_create: bool = False) -> None:
        self.raise_on_create = raise_on_create

    async def create_task(self, **_: Any) -> dict[str, Any]:
        if self.raise_on_create:
            raise RuntimeError("linear down")
        return {"ok": True, "identifier": "LIN-1", "url": "http://x/LIN-1"}


class FakeObsidian:
    def __init__(self, raise_on_write: bool = False) -> None:
        self.raise_on_write = raise_on_write

    def ensure_vault(self) -> None:  # pragma: no cover - trivial
        pass

    async def write_note(self, **_: Any) -> dict[str, Any]:
        if self.raise_on_write:
            raise RuntimeError("vault down")
        return {"ok": True, "path": "Notes/x.md"}


class FakeTranscriber:
    def __init__(self, transcript: str = "") -> None:
        self.transcript = transcript

    async def transcribe(self, _path: str) -> str:
        return self.transcript


class Capture:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


# ------------------------------------------------------------------ fixtures
@pytest.fixture
async def repo() -> Any:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set; skipping Postgres integration test")
    get_settings.cache_clear()
    try:
        await apply_schema()
    except Exception as exc:  # noqa: BLE001
        await close_pool()
        pytest.skip(f"Postgres not reachable: {exc}")
    pool = await get_pool()
    await pool.execute(
        "TRUNCATE inbox, open_asks, read_later, routing_memory, kv, llm_usage RESTART IDENTITY CASCADE"
    )
    yield Repository()
    await close_pool()


def make_engine(repo: Repository, claude: Any, linear: Any, obsidian: Any, send: Any) -> TriageEngine:
    settings = get_settings()
    return TriageEngine(
        settings=settings,
        repo=repo,
        claude=claude,
        linear=linear,
        obsidian=obsidian,
        send=send,
    )


async def _seed_pending(repo: Repository, text: str = "do the thing") -> dict[str, Any]:
    row = await repo.insert_inbox(
        source="dm", raw_type="text", content_hash=os.urandom(8).hex(), extracted_text=text
    )
    assert row is not None
    return row


# --------------------------------------------------------------------- tests
async def test_max_open_asks_is_enforced(repo: Repository) -> None:
    # Fill all four ask slots.
    for _ in range(4):
        item = await _seed_pending(repo)
        await repo.create_open_ask(inbox_id=item["id"], question="?")
    assert await repo.count_open_asks() == 4

    # A fresh pending item exists, but no slot is free.
    await _seed_pending(repo, "another")
    engine = make_engine(repo, FakeClaude([]), FakeLinear(), FakeObsidian(), Capture())
    started = await engine.maybe_advance()
    assert started is False
    assert await repo.count_open_asks() == 4  # still four, not five


async def test_ask_then_answer_then_route(repo: Repository) -> None:
    item = await _seed_pending(repo)
    claude = FakeClaude(
        [
            {"tool": T.ASK_CLARIFYING_QUESTION, "input": {"question": "Note or task?",
                                                          "suggested_answers": ["Note", "Task"]}},
            {"tool": T.CREATE_OBSIDIAN_NOTE, "input": {"title": "x"}},
        ]
    )
    send = Capture()
    engine = make_engine(repo, claude, FakeLinear(), FakeObsidian(), send)

    await engine.maybe_advance()
    assert await repo.count_open_asks() == 1
    assert (await repo.get_inbox(item["id"]))["status"] == "asking"

    ask = await repo.oldest_unanswered_ask()
    await engine.handle_owner_answer(ask, "1")  # numeric quick-reply -> "Note"

    assert await repo.count_open_asks() == 0  # slot freed
    routed = await repo.get_inbox(item["id"])
    assert routed["status"] == "routed"
    assert routed["routed_to"].startswith("obsidian:")


async def test_failed_route_resets_to_pending(repo: Repository) -> None:
    item = await _seed_pending(repo)
    claude = FakeClaude(
        [
            {"tool": T.ASK_CLARIFYING_QUESTION, "input": {"question": "?"}},
            {"tool": T.CREATE_OBSIDIAN_NOTE, "input": {"title": "x"}},
        ]
    )
    engine = make_engine(repo, claude, FakeLinear(), FakeObsidian(raise_on_write=True), Capture())

    await engine.maybe_advance()
    ask = await repo.oldest_unanswered_ask()
    await engine.handle_owner_answer(ask, "whatever")

    # Ask is closed (slot freed) but the item is back in the pending pool to retry.
    assert await repo.count_open_asks() == 0
    assert (await repo.get_inbox(item["id"]))["status"] == "pending"
    # And it's re-pickable.
    assert (await repo.next_pending_item())["id"] == item["id"]


async def test_ambiguous_is_parked_then_requeued(repo: Repository) -> None:
    item = await _seed_pending(repo)
    claude = FakeClaude([{"tool": T.MARK_AMBIGUOUS, "input": {"reason": "unclear"}}])
    engine = make_engine(repo, claude, FakeLinear(), FakeObsidian(), Capture())

    await engine.maybe_advance()
    assert (await repo.get_inbox(item["id"]))["status"] == "ambiguous"
    # Parked: the drip won't re-pick it (no infinite loop).
    assert await repo.next_pending_item() is None

    # Daily requeue (older_than_hours=0 to ignore the age gate in-test) revives it.
    moved = await repo.requeue_ambiguous(older_than_hours=0)
    assert moved == 1
    assert (await repo.next_pending_item())["id"] == item["id"]


async def test_empty_extraction_becomes_needs_attention(repo: Repository) -> None:
    pipeline = IngestPipeline(repo, FakeClaude([]), FakeTranscriber(transcript=""))
    # A voice note that transcribes to nothing.
    row = await pipeline.ingest(
        RawItem(source="dm", raw_type="voice", file_path=None, raw_ref="c:1")
    )
    assert row is not None
    assert row["status"] == "needs_attention"
    assert await repo.count_needs_attention() == 1
    # Not stuck-but-invisible: excluded from the pending pool.
    assert await repo.next_pending_item() is None


async def test_dedup_blocks_second_ingest(repo: Repository) -> None:
    pipeline = IngestPipeline(repo, FakeClaude([]), FakeTranscriber())
    first = await pipeline.ingest(RawItem(source="dm", raw_type="text", text="same body"))
    second = await pipeline.ingest(RawItem(source="saved_sweep", raw_type="text", text="same body"))
    assert first is not None
    assert second is None  # content-hash dedup
