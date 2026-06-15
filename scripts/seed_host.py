"""Register a read-only dev host for agent monitoring.

    python scripts/seed_host.py box-2 user@1.2.3.4

Production hosts must NEVER be registered here — the DB CHECK constraint and the
monitor's denylist both refuse mutating commands, but the real guarantee is that
prod simply doesn't get added.
"""

from __future__ import annotations

import asyncio
import sys

from megan.db.migrate import apply_schema
from megan.db.pool import close_pool
from megan.db.repository import Repository
from megan.monitor.registry import DEFAULT_ALLOWED_COMMANDS


async def main(name: str, alias: str) -> None:
    await apply_schema()
    repo = Repository()
    host = await repo.upsert_host(
        name=name,
        ssh_alias=alias,
        allowed_commands=DEFAULT_ALLOWED_COMMANDS,
        notes="seeded by scripts/seed_host.py",
    )
    print(f"registered host {host['name']} -> {host['ssh_alias']}")
    print("allowed (read-only) commands:")
    for cmd in host["allowed_commands"]:
        print(f"  $ {cmd}")
    await close_pool()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python scripts/seed_host.py <name> <user@host[:port]>")
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
