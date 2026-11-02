"""Arxiv PDF download."""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from .config import ArxivSettings
from .util import safe_path_component

log = logging.getLogger(__name__)


def download_pdf(
    arxiv_id: str,
    output_dir: Path,
    settings: ArxivSettings | None = None,
) -> Path:
    """Download a paper PDF from arxiv. Returns the local path."""
    if settings is None:
        settings = ArxivSettings()

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_path_component(arxiv_id)}.pdf"
    pdf_path = output_dir / filename

    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        log.debug("PDF already cached: %s", pdf_path)
        return pdf_path

    url = f"https://arxiv.org/pdf/{arxiv_id}"
    log.info("Downloading PDF: %s", url)
    resp = requests.get(
        url,
        timeout=settings.timeout_seconds,
        headers={"User-Agent": settings.user_agent},
        proxies={"http": "", "https": ""},
    )
    resp.raise_for_status()
    pdf_path.write_bytes(resp.content)
    log.info("Saved PDF: %s (%.1fMB)", pdf_path, len(resp.content) / 1024 / 1024)
    return pdf_path
