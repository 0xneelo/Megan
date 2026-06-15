# Megan

A persistent, proactive **personal assistant that lives in Telegram**. You DM
her, voice-message her, forward her links, dump screenshots. She triages
everything through conversation — *is this a task? a note? something to read
later? what project? when's it due?* — and routes the answer to **Linear**
(tasks) or **Obsidian** (notes/docs). She runs on a scheduler, drips your backlog
to you slowly, reminds you, and can SSH into your dev boxes (read-only) to tell
you what your coding agents are up to.

Built on the **Anthropic API** (Claude Haiku for cheap classification, Claude
Opus for triage reasoning, vision, doc-writing, and agent summaries).

> Megan is the assistant; the codebase is the service that runs her.

---

## What she does

- **Lives on a burner Telegram account** you chat with like a person.
- **Ingests** your DMs, forwards, Saved Messages, and uploaded files.
- **Understands screenshots** via Claude vision (OCR + intent).
- **Transcribes voice notes** (OpenAI Whisper API or local `whisper.cpp`).
- **Triages through conversation**, asking *one* question at a time and never
  holding more than **4 open questions at once** (enforced in code).
- **Routes** to Linear or Obsidian via Claude tool-use.
- **Is proactive**: backlog drip, morning brief, reminders, read-later digests —
  all quiet-hours aware and globally rate-limited.
- **Monitors dev agents read-only** over SSH, summarizing what they're doing.
- **Never touches production. Never does the work — she organizes and reminds.**

---

## Architecture

```
            ┌───────────────────────── VPS (always-on) ──────────────────────────┐
            │  Telethon userbot   APScheduler        Agent monitor (read-only SSH)│
            │       │                  │                       │                  │
            │       └──────────────────┼───────────────────────┘                  │
            │                          ▼                                           │
            │                   ┌──────────────┐                                   │
            │                   │ Orchestrator │  the brain — routes events,       │
            │                   │  (asyncio)   │  calls Claude, enforces ≤4 asks   │
            │                   └──────┬───────┘                                   │
            │        ┌─────────────────┼──────────────┬───────────────┐           │
            │        ▼                 ▼              ▼               ▼           │
            │     Claude API        Postgres        Linear         Obsidian       │
            │  (classify/triage/   (inbox + state,  (tasks)       (git vault,     │
            │   vision/summaries)   source of truth)               notes/docs)    │
            └────────────────────────────────────────────────────────────────────┘
```

The ingestion pipeline is a single funnel:

```
ingest → dedup → extract → classify → enqueue → triage(conversational) → route
```

Every inbound item is written to the Postgres `inbox` **immediately**, before any
LLM or downstream call, so nothing is lost if something is down. Dedup is
content-hash based, so re-reading Saved Messages or re-running an export never
creates duplicates. **Postgres is the source of truth; Linear and Obsidian are
mirrors.**

### Code map

| Path | Role |
|---|---|
| `src/megan/orchestrator.py` | The brain. Wires events, owns quiet-hours + cost-cap guards. |
| `src/megan/telegram/` | Telethon userbot (transport only). |
| `src/megan/ingest/` | Pipeline, dedup, link/PDF extraction, transcription. |
| `src/megan/llm/` | All Claude calls + the triage tool schemas (the routing contract). |
| `src/megan/triage/` | Conversational triage engine + the ≤4-asks rule. |
| `src/megan/routing/` | Linear (GraphQL) and Obsidian (git vault) writers. |
| `src/megan/scheduler/` | APScheduler proactive jobs. |
| `src/megan/monitor/` | Read-only SSH agent monitoring. |
| `src/megan/db/` | asyncpg pool, schema migration, repository (all SQL). |
| `sql/schema.sql` | The data model. |

---

## The hard risk you're accepting

Megan uses the **userbot-on-a-burner** approach. Be clear-eyed:

- **Automating a user account violates Telegram's ToS.** The burner can be banned
  with no warning and no appeal. Treat it as disposable: never tie it to your real
  identity, never make it the only place data lives.
- **Mitigations baked in:** conservative polling, human-like jittered send pacing,
  every ingested item persisted to your own Postgres immediately (the account is a
  *transport*, not a datastore), and content-hash dedup so a re-export after a ban
  loses nothing.
- **Fallback if the account dies:** switch to *periodic-export mode* — drop a
  Telegram export file in and ingest it through the same idempotent pipeline (the
  `upload` source path). Because dedup is content-addressed, re-ingesting is safe.
- Your **real** account is never automated. Forward things to the burner, or
  periodically export and drop the file in.

### A note on inline keyboards

The spec wants tappable buttons. **Telegram userbots (MTProto user accounts)
cannot send Bot-API inline keyboards — only bot accounts can.** So Megan renders
quick answers as **numbered options** you tap/type (`1`, `2`, …); free-text and
voice answers are always accepted and parsed too. If you later want real inline
keyboards, the documented de-risk path is to run in **bot-API mode (aiogram)** —
that trades the Saved-Messages auto-sweep for ToS-compliance and real buttons.

---

## Setup

### 1. Prerequisites
- Python 3.11+
- PostgreSQL 14+
- A **burner** Telegram account on an SMS-receivable number
- Telegram API credentials from <https://my.telegram.org>
- An Anthropic API key; optionally an OpenAI key (Whisper) and a Linear key

### 2. Configure
```bash
cp .env.example .env
# fill in TELEGRAM_*, ANTHROPIC_API_KEY, OWNER_TELEGRAM_ID, DATABASE_URL, …
```
`OWNER_TELEGRAM_ID` is *your real* numeric Telegram id — Megan only talks to you.

### 3a. Run with Docker (recommended)
```bash
docker compose up --build
```
First run will prompt for the burner's login code **in the container logs** — run
`docker compose run --rm megan megan` once interactively to complete sign-in and
create the `megan.session` file, then `docker compose up -d`.

### 3b. Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# Postgres must be reachable at DATABASE_URL; schema is applied on startup.
megan          # or:  python -m megan
```

### 4. Register dev hosts (optional, for agent monitoring)
```bash
python scripts/seed_host.py box-2 user@1.2.3.4
```
Then ask Megan: `/agent box-2`. **Never register a production host.**

---

## Talking to Megan

Just send her things. She'll acknowledge, sort, and ask what she needs. Commands:

| Command | Does |
|---|---|
| `/status` | What's pending, open questions, read-later count, monthly spend |
| `/brief` | Today's brief on demand |
| `/agents` | Summarize all registered dev hosts |
| `/agent <name>` | Summarize one dev host |
| `/hosts` | List monitored hosts |
| `/help` | The menu |

Example triage exchange:
```
You:  (forward a @swyx thread on eval harnesses)
Megan: Got it (read_later). Let me sort it.
Megan: You saved a thread on eval harnesses. Read-later, or is there a task in it?
       1) Read later
       2) It's a task
       3) Just a note
       4) Drop it
You:  1
Megan: Got it — added to your read-later.
```

---

## Build phases

The codebase implements the spec's phases:

1. **Reactive core** — userbot → Postgres inbox → Claude classify → reply.
   Voice transcription + screenshot vision. ✅
2. **Routing** — triage engine with tool-use + numbered quick replies; Linear and
   Obsidian writers. ✅
3. **Saved Messages ingestion** — periodic idempotent sweep, dedup, upload path. ✅
4. **Proactivity** — APScheduler, the ≤4-asks enforcement, backlog drip, morning
   brief, reminders, quiet hours. ✅
5. **Agent monitoring** — host registry, read-only SSH, Claude summaries. ✅
6. **Polish** — routing-memory (Claude learns your patterns) is wired; read-later
   digests done; pgvector semantic search over notes is the next step. 🚧

---

## Configuration reference

See `.env.example` for the full list. Highlights:

| Var | Meaning |
|---|---|
| `MAX_OPEN_ASKS` | Hard cap on simultaneous open questions (default 4). |
| `QUIET_HOURS_START` / `END` | No proactive pings in this window. |
| `WORK_HOURS_START` / `END` | When the backlog drip is active. |
| `BACKLOG_DRIP_MINUTES` | How often to surface the next item, slot permitting. |
| `MEGAN_MONTHLY_COST_CAP_USD` | Soft Anthropic spend cap; proactivity pauses past it. |
| `TRANSCRIBE_PROVIDER` | `openai` \| `local` \| `none`. |
| `OBSIDIAN_VAULT_PATH` | Git-backed markdown vault Megan writes into. |
| `MONITOR_SSH_KEY_PATH` | **Low-privilege** key that only reaches dev boxes. |

---

## Safety guarantees

- **No production access.** Production hosts are never in the registry; there is
  no code path that writes to a remote host. The DB has a `CHECK (is_production =
  false)` constraint, the monitor refuses any non-read-only command (allowlist +
  mutating-verb denylist), and the monitor key should be a separate low-privilege
  keypair.
- **No autonomous execution.** Megan organizes and reminds; she never does the
  work itself.
- **Data durability.** Postgres is the source of truth; a Telegram ban or an
  Obsidian sync hiccup never loses data.

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```
The test suite covers the pure logic that doesn't need a DB or network: dedup
hashing, due-date parsing, the quick-reply rendering/parsing, the read-only
command guard, and config defaults.

---

## License

MIT.
