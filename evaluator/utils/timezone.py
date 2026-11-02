from datetime import tzinfo

import pytz


def get_timezone(name: str = "Asia/Shanghai", fallback: str = "UTC") -> tzinfo:
    """Return a timezone object, falling back safely when the requested zone is unavailable."""
    try:
        return pytz.timezone(name)
    except Exception:
        try:
            return pytz.timezone(fallback)
        except Exception:
            return pytz.UTC
