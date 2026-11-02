"""PDF to markdown conversion via Somark API."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

from .config import SomarkSettings
from .util import safe_path_component

log = logging.getLogger(__name__)

_BASE64_IMG_RE = re.compile(
    r"!\[([^\]]*)\]\(data:image/[^;]+;base64,[A-Za-z0-9+/=\s]+\)"
)


def _strip_base64_images(markdown: str) -> str:
    """Remove base64-encoded images from markdown to reduce size."""
    return _BASE64_IMG_RE.sub(r"[Image: \1]", markdown)


def convert_pdf_to_markdown(
    pdf_path: Path,
    settings: SomarkSettings,
    cache_dir: Optional[Path] = None,
    max_retries: int = 3,
) -> str:
    """Convert PDF to markdown via Somark API.

    Returns the markdown text, or raises on failure.
    """
    if cache_dir:
        cache_path = cache_dir / f"{safe_path_component(pdf_path.stem)}.md"
        if cache_path.exists():
            log.debug("Using cached markdown: %s", cache_path)
            return cache_path.read_text(encoding="utf-8")

    for attempt in range(max_retries):
        try:
            with open(pdf_path, "rb") as f:
                files = {"file": (pdf_path.name, f)}
                data = {
                    "output_formats": "markdown",
                    "extract_config": json.dumps(settings.extract_config),
                }
                log.info("Calling Somark API for %s (attempt %d)", pdf_path.name, attempt + 1)
                resp = requests.post(
                    settings.api_url,
                    auth=settings.auth,
                    files=files,
                    data=data,
                    timeout=1800,
                    proxies={"http": "", "https": ""},
                )
                resp.raise_for_status()

            md = resp.json().get("outputs", {}).get("markdown", "")
            if not md or not md.strip():
                raise ValueError(f"Empty markdown from Somark for {pdf_path.name}")

            md = _strip_base64_images(md)

            if cache_dir:
                cache_path = cache_dir / f"{safe_path_component(pdf_path.stem)}.md"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(md, encoding="utf-8")

            return md

        except Exception as e:
            log.warning("Somark attempt %d failed for %s: %s", attempt + 1, pdf_path.name, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Somark conversion failed after {max_retries} attempts for {pdf_path.name}")
