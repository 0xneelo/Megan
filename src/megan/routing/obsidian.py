"""Obsidian vault writer — notes/docs land as markdown in a git-backed vault.

Git gives version history + cross-device sync. Writes are committed (and optionally
pushed) so the owner's other devices pick them up; a cron job also pulls/pushes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from megan.config import Settings

log = logging.getLogger("megan.routing.obsidian")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug[:60] or "note"


class ObsidianVault:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.obsidian_vault_path).expanduser()

    def ensure_vault(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    async def write_note(
        self,
        *,
        folder: str,
        title: str,
        body: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Write a markdown note. Returns {ok, path}."""
        self.ensure_vault()
        folder_path = self.root / (folder or "Notes")
        folder_path.mkdir(parents=True, exist_ok=True)

        filename = f"{date.today().isoformat()}-{_slugify(title)}.md"
        path = folder_path / filename

        frontmatter = ["---", f'title: "{title}"', f"created: {date.today().isoformat()}"]
        if tags:
            frontmatter.append("tags: [" + ", ".join(tags) + "]")
        frontmatter.append("---\n")

        content = "\n".join(frontmatter) + f"# {title}\n\n{body.strip()}\n"
        path.write_text(content, encoding="utf-8")

        rel = str(path.relative_to(self.root))
        if self.settings.obsidian_git_autocommit:
            await self._git_commit(f"megan: add {rel}")
        log.info("wrote note %s", rel)
        return {"ok": True, "path": rel}

    async def _git_commit(self, message: str) -> None:
        if not (self.root / ".git").exists():
            log.debug("vault is not a git repo; skipping commit")
            return
        await self._git("add", "-A")
        # commit may fail if nothing changed; tolerate it.
        await self._git("commit", "-m", message, check=False)
        await self._git("push", check=False)

    async def _git(self, *args: str, check: bool = True) -> None:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.root),
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if check and proc.returncode != 0:
            log.warning("git %s failed: %s", args[0], stderr.decode(errors="replace")[:300])

    async def git_sync(self) -> None:
        """Pull then push — used by the cron job for cross-device sync."""
        if not (self.root / ".git").exists():
            return
        await self._git("pull", "--rebase", check=False)
        await self._git("push", check=False)
