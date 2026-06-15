# Megan → Production: agent build-out plan

**Goal for this run:** take the working Megan codebase (reactive spine + triage +
routing + proactivity + read-only monitoring, all green on `pytest`) and harden it
into a service that can be **installed and run in production tomorrow** —
reliable, observable, secure, and ready to scale to more load without a rewrite.

You have a few hours. Work top-down: **P0 ships, P1 hardens, P2 scales.** Don't
start P2 until P0+P1 are green. Commit in small, reviewable increments.

---

## Operating rules (do not violate)

1. **Never write to a remote host.** The monitor is read-only by construction;
   keep the allowlist + mutating-verb denylist (`src/megan/monitor/registry.py`)
   and the `CHECK (is_production = false)` constraint. No prod hosts in the
   registry, ever.
2. **Keep the ≤4-open-asks guarantee.** It's the product's core promise. Any
   refactor must preserve it and the tests in `tests/test_triage_state_machine.py`.
3. **Postgres is the source of truth.** Linear/Obsidian/Telegram are mirrors or
   transport. No change may make a Telegram ban or API outage lose data.
4. **Don't break the suite.** After every change: `pytest -q && ruff check src tests`
   must pass, and `./scripts/run_integration_tests.sh` (ephemeral Postgres) must
   stay green. Add tests for everything you build.
5. **Secrets never enter the repo or logs.** Redact tokens; load from env / a
   secrets manager.
6. Don't push to `main` without confirmation; develop on a feature branch.

**Definition of done for the run:** a tagged release that boots from a clean
machine via `docker compose up` (or the systemd unit), validates its config,
applies migrations, serves Megan, exposes `/healthz` + `/metrics`, recovers from
restarts without data loss or duplicate sends, and has CI running unit +
integration + lint + type-check on every push.

---

## P0 — Install-tomorrow blockers (do these first)

### P0.1 Onboarding & config validation
- Add `scripts/login.py` that signs the burner in interactively and prints a
  **Telethon StringSession** to paste into `.env` (`TELEGRAM_STRING_SESSION`), so
  prod never needs an interactive TTY. Document in README.
- On boot (`Orchestrator.start`), **fail fast with a clear message** if required
  config is missing or malformed (Telegram creds, `ANTHROPIC_API_KEY`,
  `DATABASE_URL`, `OWNER_TELEGRAM_ID`). Add a `megan doctor` subcommand that
  checks: DB reachable + schema applied, Anthropic key valid (cheap ping),
  Telegram authorized, Linear reachable (if configured), vault path writable.
- **Acceptance:** `megan doctor` exits non-zero with actionable errors when
  anything is misconfigured; clean machine → green.

### P0.2 Migrations as a first-class tool
- Replace the single `apply_schema()` with a versioned migration runner (yoyo or
  alembic). Keep `sql/schema.sql` as migration `0001`. Add a `megan migrate`
  command; run it on boot and in the Docker entrypoint.
- **Acceptance:** migrating an empty DB and an existing DB both succeed; a second
  run is a no-op.

### P0.3 Crash-safe restart & graceful shutdown
- Ensure a restart never loses in-flight work or double-sends: ingestion already
  writes raw rows before processing; verify the **saved-sweep cursor**
  (`last_saved_id`) only advances after successful ingest, and make reminder/brief
  sends idempotent per day (reminders already dedup — extend the same pattern to
  morning brief and read-later nudge via a `kv` "sent today" marker).
- Harden `Orchestrator.shutdown`: drain the scheduler, close the Telethon client
  and the pool cleanly. Add `Restart=always` is already in the systemd unit —
  verify the Docker `restart: unless-stopped`.
- **Acceptance:** kill -9 the process mid-triage; on restart, no duplicate
  Telegram messages, no lost inbox rows, pending items resume.

### P0.4 Telegram robustness
- Catch `telethon.errors.FloodWaitError` around sends and sweeps; back off for the
  requested seconds. Wrap sends in a small queue with global pacing so bursts
  can't trip flood limits.
- Handle **grouped media (albums)**: assemble `grouped_id` messages into one
  inbox item instead of N.
- Handle the answer-vs-new-item ambiguity (README-known): when replying to a
  question, prefer Telegram **reply-to threading** — tag each question message id
  on its `open_asks` row, and match an inbound reply to the specific ask via
  `message.reply_to`. Fall back to "oldest open ask" only when no reply target.
- **Acceptance:** sending an album, replying to a specific question, and a flood
  burst all behave correctly (unit tests with a faked client + one manual check).

### P0.5 Cost cap that actually caps
- Gate the **reactive** path on the monthly cap too (classify/vision/triage),
  not just proactivity. When over cap: stop spending, tell the owner once, queue
  items as `pending` so they process after reset. Make `_PRICING` in
  `src/megan/llm/client.py` config-driven and add a unit test.
- **Acceptance:** with a tiny cap, Megan stops calling Claude and says so; under
  cap, normal.

### P0.6 Error surfacing & dead-letter
- Add a global error boundary: any unhandled exception in an inbound handler or a
  scheduled job is logged with context and, if it affects the owner's request,
  produces a short "I hit an error on X" message (rate-limited). Add an
  `inbox.status = 'error'` + `meta.error` for items that repeatedly fail routing
  (after N retries) so they're visible in `/status`, not silently retried forever.
- **Acceptance:** a forced exception in routing lands the item in `error`, pings
  the owner once, and shows up in `/status`.

---

## P1 — Robustness & operability

### P1.1 Observability
- Structured JSON logging (swap `logging_setup.py` to a JSON formatter; include a
  request/item id). Redact tokens.
- Expose a small `aiohttp`/`starlette` admin server on a private port:
  `/healthz` (liveness: pool + telethon connected), `/readyz`, `/metrics`
  (Prometheus). Counters/histograms: items ingested, triaged, routed by
  destination, asks open (gauge), LLM calls + tokens + cost by purpose, Telegram
  send latency, errors.
- **Acceptance:** `curl /metrics` shows live counters; `/healthz` flips to 503
  when the DB is down.

### P1.2 Retry/backoff & idempotency everywhere
- Wrap Linear and link-fetch calls with bounded retries + jittered backoff (the
  Anthropic SDK already retries 429/5xx — confirm `max_retries`). Make Obsidian
  git operations resilient to a dirty/locked repo.
- Add a periodic **retry sweep**: pick `pending` items that failed routing and
  re-triage; cap attempts via `meta.attempts`, then → `error`.
- **Acceptance:** Linear returning 500 thrice then 200 results in exactly one
  task and no dupes.

### P1.3 Media & data lifecycle
- Clean up `downloads/` after successful ingest (the bytes are in Postgres-derived
  text; keep raw only if configured). Add a retention job for old `llm_usage`,
  resolved `inbox`, and downloaded media.
- **Downscale images** before vision to cap tokens/cost; map more media types.
- **Acceptance:** disk doesn't grow unbounded under a soak test; vision cost per
  image is bounded.

### P1.4 LLM quality & safety
- Validate structured outputs against the schema; on parse failure, one retry,
  then `ambiguous`. Handle `stop_reason == "refusal"` from triage gracefully.
- Add **prompt caching** on the stable persona/system prefix to cut cost (see the
  persona prompts in `src/megan/persona.py` — they're already stable; add a
  `cache_control` breakpoint on the system block).
- Build a tiny **triage eval harness**: a fixture set of items → expected routes;
  run it in CI to catch prompt regressions. Track accuracy.
- **Acceptance:** eval harness runs in CI; cache hit-rate visible in metrics.

### P1.5 Security hardening
- Docker: multi-stage, non-root user, pinned deps (hash-locked
  `requirements.txt` via `pip-tools`), read-only rootfs where possible,
  healthcheck. systemd: `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, restrict the SSH key path.
- Document and implement the **periodic-export fallback** (`source='upload'`) so a
  banned burner doesn't stop ingestion: a `megan import <telegram-export.json>`
  command that runs the same idempotent pipeline.
- Move secrets to a manager (SOPS/age, Docker secrets, or env from the host
  vault); never bake into images.
- **Acceptance:** image runs as non-root; `megan import` ingests an export with
  zero duplicates on re-run.

### P1.6 CI/CD
- GitHub Actions: on push/PR run `ruff`, `mypy` (add type hints / a `mypy`
  config), `pytest` (unit), and `scripts/run_integration_tests.sh` (services:
  postgres). Cache deps. Build the Docker image on tag.
- Add `pre-commit` (ruff, end-of-file, trailing-whitespace).
- **Acceptance:** a PR shows all checks; a tag publishes an image.

---

## P2 — Scale & multi-process

> Only after P0+P1 are green. The current design is correct for **one process**;
> these changes make it horizontally scalable without losing the ≤4 guarantee.

### P2.1 Split the workloads
- Separate processes/roles behind one image: `telegram` (transport), `worker`
  (ingest + triage), `scheduler` (proactivity, single leader). Share Postgres.
- Use **Postgres `LISTEN/NOTIFY`** (or Redis/RQ) as the work queue between
  transport and workers; the inbox table is already the durable queue — add a
  `claimed_by`/`claimed_at` lease with `FOR UPDATE SKIP LOCKED` so multiple
  workers pull without double-processing.

### P2.2 Distributed ≤4 and single-leader scheduling
- Replace the in-process `asyncio.Lock` in `TriageEngine` with a **Postgres
  advisory lock** (`pg_advisory_xact_lock`) around the slot-check + ask-insert so
  the cap holds across workers.
- Elect a single scheduler leader (advisory lock / `pg_try_advisory_lock`) so
  cron jobs don't fire N times.
- **Acceptance:** run 3 workers under load; open asks never exceed
  `MAX_OPEN_ASKS`; each cron fires once. Add a concurrency test.

### P2.3 Throughput & cost at scale
- Batch/parallelize ingestion with bounded concurrency; rate-limit Anthropic
  per-minute. Consider the Batches API for non-interactive classification of large
  saved-message backfills (50% cheaper).
- Add pgvector (Phase 6) for semantic search over notes and smarter
  routing-memory recall.

---

## Suggested cadence

- **Hour 1:** P0.1–P0.3 (onboarding, migrations, crash-safe restart). Get a clean
  `docker compose up` boot + `megan doctor` green.
- **Hour 2:** P0.4–P0.6 (Telegram robustness, real cost cap, error/dead-letter) +
  P1.1 observability. Now it's operable.
- **Hour 3:** P1.2–P1.6 (retries, lifecycle, LLM quality, security, CI). Tag a
  release candidate.
- **Stretch:** start P2.1–P2.2 only if time remains; otherwise document them as
  the scale roadmap.

## Verify before you stop

```bash
pytest -q                       # unit
ruff check src tests            # lint
mypy src                        # types (once added)
./scripts/run_integration_tests.sh   # integration on ephemeral Postgres
megan doctor                    # config + connectivity
docker compose up --build       # clean-boot smoke
curl -fsS localhost:PORT/healthz # liveness
```

Tag the release, update `README.md` (ops/runbook section), and write a one-page
`RUNBOOK.md`: how to deploy, rotate the burner, restore from backup, and what each
alert means. Leave the repo greener than you found it.
