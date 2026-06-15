"""Content extraction for links and files.

- Link  -> fetch page, pull readable title + body (don't crawl aggressively).
- PDF   -> parse to text.
- Plain file -> read text.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

log = logging.getLogger("megan.ingest.extract")

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_USER_AGENT = "MeganBot/0.1 (+personal-assistant; respectful-fetch)"


def find_first_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0) if m else None


async def fetch_link(url: str, timeout: float = 15.0) -> dict[str, str]:
    """Fetch a URL and return {title, text, url}. Best-effort; never raises."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:  # noqa: BLE001 - best-effort fetch
        log.warning("fetch failed for %s: %s", url, exc)
        return {"title": url, "text": "", "url": url}

    title, body = _readable(html)
    return {"title": title or url, "text": body, "url": url}


def _readable(html: str) -> tuple[str, str]:
    """Extract a readable title + main text, degrading gracefully."""
    try:
        from readability import Document  # readability-lxml

        doc = Document(html)
        title = (doc.short_title() or "").strip()
        summary_html = doc.summary(html_partial=True)
    except Exception:  # noqa: BLE001
        title, summary_html = "", html

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(summary_html or html, "lxml")
        if not title:
            t = soup.find("title")
            title = t.get_text(strip=True) if t else ""
        text = soup.get_text("\n", strip=True)
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", summary_html or html)

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text[:20000]


def extract_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF extract failed for %s: %s", path, exc)
        return ""


def extract_file(path: str) -> str:
    """Extract text from an uploaded file by extension; PDFs handled specially."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return extract_pdf(path)
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:50000]
    except Exception as exc:  # noqa: BLE001
        log.warning("file read failed for %s: %s", path, exc)
        return ""
