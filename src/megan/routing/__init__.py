"""Routing — final destinations for triaged items.

Linear and Obsidian are MIRRORS; Postgres is the source of truth. A failed write
here never loses the item — it stays in the inbox to retry.
"""

from megan.routing.linear import LinearClient
from megan.routing.obsidian import ObsidianVault

__all__ = ["LinearClient", "ObsidianVault"]
