"""
Secret and configuration bootstrap for local development.

Call ``bootstrap_secrets()`` once at process startup (before any module reads
``os.getenv``).  It populates ``os.environ`` from a ``.env`` file if present,
and applies non-secret configuration defaults.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Non-secret defaults ────────────────────────────────────────────────────
# Applied only when the variable is unset; an explicit env var always wins.
_CONFIG_DEFAULTS = {
    # Cutoff-aware search/fetch service (see docs/search_backend.md).
    "WEB_TOOLS_SERVICE_URL": "http://localhost:8900",
}

_bootstrapped = False


def bootstrap_secrets() -> None:
    """Populate ``os.environ`` with secrets from .env and config defaults.

    Safe to call multiple times; only the first invocation takes effect.
    """
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True

    loaded_from = _try_dotenv()

    # Apply non-secret defaults (never overwrite values that are already set).
    for key, default in _CONFIG_DEFAULTS.items():
        if not os.environ.get(key):
            os.environ[key] = default

    if loaded_from:
        logger.info("Secret bootstrap complete (source: %s)", loaded_from)
    else:
        logger.warning(
            "Secret bootstrap: no .env file found. "
            "Secrets must already be present in the environment."
        )


def _try_dotenv() -> str | None:
    """Load available ``.env`` files. Returns source labels or None."""
    # This file lives in evaluator/runtime/. Prefer the repo-root .env, then let
    # evaluator/.env fill evaluator-specific keys without overriding anything.
    ranker_root = Path(__file__).resolve().parent.parent
    repo_root = ranker_root.parent
    candidates = [
        repo_root / ".env",
        ranker_root / ".env",
    ]
    loaded: list[str] = []
    for dotenv_path in candidates:
        if dotenv_path.is_file():
            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=dotenv_path, override=False)
                logger.info("Loaded .env from %s", dotenv_path)
                loaded.append(f"dotenv:{dotenv_path}")
            except ImportError:
                logger.debug("python-dotenv not installed; skipping .env loading.")
                return None
    return ", ".join(loaded) if loaded else None
