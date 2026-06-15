#!/usr/bin/env bash
# Spin up a throwaway Postgres cluster, run the integration tests against it,
# and tear it down. Useful in CI and locally.
#
#   ./scripts/run_integration_tests.sh
#
# Honors an existing $DATABASE_URL (skips the ephemeral cluster) if set.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Using existing DATABASE_URL"
  exec pytest tests/test_triage_state_machine.py -q
fi

PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)"
if [[ -z "$PGBIN" ]]; then
  echo "Postgres server binaries not found; install postgresql to run integration tests." >&2
  exit 1
fi

TMP="$(mktemp -d)"
PORT="${PGPORT:-55432}"
RUN_AS=""
# postgres refuses to run as root; drop to the postgres user if we are root.
if [[ "$(id -u)" -eq 0 ]] && getent passwd postgres >/dev/null; then
  RUN_AS="runuser -u postgres --"
  chown -R postgres "$TMP"
fi

cleanup() {
  $RUN_AS "$PGBIN/pg_ctl" -D "$TMP/data" -w stop >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

$RUN_AS "$PGBIN/initdb" -D "$TMP/data" -U postgres --auth=trust >/dev/null
$RUN_AS "$PGBIN/pg_ctl" -D "$TMP/data" \
  -o "-k $TMP -p $PORT -c listen_addresses=''" -w start >/dev/null
$RUN_AS "$PGBIN/createdb" -h "$TMP" -p "$PORT" -U postgres megan_test

export DATABASE_URL="postgresql://postgres@/megan_test?host=$TMP&port=$PORT"
pytest tests/test_triage_state_machine.py -q
