"""Voice-note transcription.

Telegram voice notes are OGG/Opus. Claude has no audio input, so transcription
is a separate hop. Provider is configurable: OpenAI Whisper API (trivial, costs
per minute) or local whisper.cpp (free, private, needs a beefier box).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from megan.config import Settings

log = logging.getLogger("megan.ingest.transcribe")


class Transcriber:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, audio_path: str) -> str:
        provider = self.settings.transcribe_provider
        if provider == "openai":
            return await self._openai(audio_path)
        if provider == "local":
            return await self._whisper_cpp(audio_path)
        log.warning("transcription disabled (provider=%s)", provider)
        return ""

    async def _openai(self, audio_path: str) -> str:
        if not self.settings.openai_api_key:
            log.warning("OPENAI_API_KEY not set; cannot transcribe")
            return ""
        # Imported lazily so the dependency is only needed when this path runs.
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        with open(audio_path, "rb") as fh:
            resp = await client.audio.transcriptions.create(
                model=self.settings.openai_whisper_model,
                file=fh,
            )
        return (resp.text or "").strip()

    async def _whisper_cpp(self, audio_path: str) -> str:
        bin_path = self.settings.whisper_cpp_bin
        model_path = self.settings.whisper_cpp_model
        if not bin_path or not model_path:
            log.warning("whisper.cpp not configured")
            return ""
        out_base = str(Path(audio_path).with_suffix(""))
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "-m",
            model_path,
            "-f",
            audio_path,
            "-otxt",
            "-of",
            out_base,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error("whisper.cpp failed: %s", stderr.decode(errors="replace")[:500])
            return ""
        txt_file = Path(out_base + ".txt")
        if txt_file.exists():
            return txt_file.read_text(encoding="utf-8", errors="replace").strip()
        return ""
