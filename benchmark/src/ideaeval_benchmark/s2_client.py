"""Semantic Scholar API client with rate limiting and multi-key support."""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher

import requests

from .config import S2Settings
from .models import S2Metadata

S2_API = "https://api.semanticscholar.org/graph/v1"
S2_LOOKUP_FIELDS = "paperId,title,citationCount,year,publicationDate,externalIds"

log = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket rate limiter."""

    def __init__(self, rps: float):
        self._lock = threading.Lock()
        self._min_gap = 1.0 / rps
        self._next_slot = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.time()
            if now < self._next_slot:
                wait = self._next_slot - now
                self._next_slot += self._min_gap
            else:
                wait = 0
                self._next_slot = now + self._min_gap
        if wait > 0:
            time.sleep(wait)


def _normalize_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = t.lower().strip()
    t = re.sub(r"[^a-z0-9 ]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _title_similarity(a: str, b: str) -> float:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


class S2Client:
    """Thread-safe Semantic Scholar API client with rate limiting and backoff."""

    def __init__(self, settings: S2Settings):
        self._settings = settings
        self._local = threading.local()
        self._key_index = 0
        self._key_lock = threading.Lock()

        # Create per-key rate limiters for multi-key mode
        if settings.api_keys:
            self._per_key_limiters = {
                key: RateLimiter(settings.rps) for key in settings.api_keys
            }
        else:
            self._per_key_limiters = {}
        self._global_limiter = RateLimiter(settings.rps)

    def _get_session(self) -> requests.Session:
        if not hasattr(self._local, "sess"):
            s = requests.Session()
            if self._settings.api_keys:
                with self._key_lock:
                    key = self._settings.api_keys[self._key_index % len(self._settings.api_keys)]
                    self._key_index += 1
                s.headers["x-api-key"] = key
            self._local.sess = s
        return self._local.sess

    def _get_limiter(self) -> RateLimiter:
        if self._per_key_limiters:
            sess = self._get_session()
            key = sess.headers.get("x-api-key", "")
            if key in self._per_key_limiters:
                return self._per_key_limiters[key]
        return self._global_limiter

    def get(self, path: str, params: dict | None = None, max_retries: int = 5) -> dict | None:
        url = f"{S2_API}/{path}"
        sess = self._get_session()
        limiter = self._get_limiter()
        for attempt in range(max_retries):
            limiter.acquire()
            try:
                r = sess.get(url, params=params, timeout=30)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return None
                if r.status_code in (429, 403):
                    wait = min(30, 2 * 2 ** attempt)
                    log.debug("%d on %s, backoff %ds", r.status_code, path, wait)
                    time.sleep(wait)
                    continue
                log.warning("HTTP %d: %s - %s", r.status_code, path, r.text[:300])
                time.sleep(2 * (attempt + 1))
            except requests.RequestException as e:
                log.warning("Network error on %s: %s", path, e)
                time.sleep(2 * (attempt + 1))
        return None

    def get_citations(
        self,
        s2_paper_id: str,
        fields: str = "paperId,title,publicationDate,year",
        limit: int = 1000,
    ) -> list[dict]:
        """Fetch all papers that cite the given paper, with pagination."""
        all_citations: list[dict] = []
        offset = 0
        while True:
            data = self.get(
                f"paper/{s2_paper_id}/citations",
                {"fields": fields, "limit": limit, "offset": offset},
            )
            if not data:
                break
            batch = data.get("data", [])
            if not batch:
                break
            for item in batch:
                citing = item.get("citingPaper", item)
                all_citations.append(citing)
            if len(batch) < limit:
                break
            offset += limit
        return all_citations

    def resolve_s2_id(self, title: str, arxiv_id: str | None = None) -> S2Metadata:
        """Resolve a paper to its Semantic Scholar ID.

        3-step fallback: arxiv lookup -> strict title match -> search + verify.
        """
        # Step 1: arXiv ID lookup
        if arxiv_id:
            data = self.get(f"paper/ARXIV:{arxiv_id}", {"fields": S2_LOOKUP_FIELDS})
            if data and data.get("paperId"):
                return S2Metadata(
                    s2_paper_id=data["paperId"],
                    s2_title=data.get("title"),
                    citation_count=data.get("citationCount", 0),
                    year=data.get("year"),
                    publication_date=data.get("publicationDate"),
                    match_method="arxiv",
                )

        # Step 2: strict title match
        data = self.get("paper/search/match", {"query": title, "fields": S2_LOOKUP_FIELDS})
        if data:
            paper = data
            if isinstance(data.get("data"), list) and data["data"]:
                paper = data["data"][0]
            if paper.get("paperId"):
                return S2Metadata(
                    s2_paper_id=paper["paperId"],
                    s2_title=paper.get("title"),
                    citation_count=paper.get("citationCount", 0),
                    year=paper.get("year"),
                    publication_date=paper.get("publicationDate"),
                    match_method="title_match",
                )

        # Step 3: keyword search + similarity verification
        data = self.get("paper/search", {"query": title, "fields": S2_LOOKUP_FIELDS, "limit": 5})
        if data and isinstance(data.get("data"), list):
            for candidate in data["data"]:
                cand_title = candidate.get("title")
                if not candidate.get("paperId") or not cand_title:
                    continue
                sim = _title_similarity(title, cand_title)
                if sim >= self._settings.search_verify_threshold:
                    return S2Metadata(
                        s2_paper_id=candidate["paperId"],
                        s2_title=cand_title,
                        citation_count=candidate.get("citationCount", 0),
                        year=candidate.get("year"),
                        publication_date=candidate.get("publicationDate"),
                        match_method=f"search_verified:{sim:.3f}",
                    )

        return S2Metadata()
