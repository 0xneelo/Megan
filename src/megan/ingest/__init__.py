"""Ingestion: ingest -> dedup -> extract -> classify -> enqueue.

Everything funnels through one inbox queue in Postgres. The raw row is written
immediately (before extraction/LLM) so nothing is lost if something downstream
is down, and dedup is content-hash based so re-reads never duplicate.
"""

from megan.ingest.pipeline import IngestPipeline, RawItem

__all__ = ["IngestPipeline", "RawItem"]
