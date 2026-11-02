from __future__ import annotations

import copy
from typing import Any, Dict, List

TOKEN_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_token_usage(token_usage: Any) -> Dict[str, int]:
    usage = token_usage if isinstance(token_usage, dict) else {}
    return {
        field: _safe_int(usage.get(field, 0))
        for field in TOKEN_USAGE_FIELDS
    }


def aggregate_token_usages(token_usages: Any) -> Dict[str, int]:
    totals = {field: 0 for field in TOKEN_USAGE_FIELDS}
    if not isinstance(token_usages, list):
        return totals

    for usage in token_usages:
        normalized = normalize_token_usage(usage)
        for field in TOKEN_USAGE_FIELDS:
            totals[field] += normalized[field]
    return totals


def build_raw_token_usage_records(
    *,
    role: str,
    model_name: str,
    raw_token_usages: Any,
    agent_name: str = "",
) -> List[Dict[str, Any]]:
    if not isinstance(raw_token_usages, list):
        return []

    records: List[Dict[str, Any]] = []
    for usage in raw_token_usages:
        if not isinstance(usage, dict) or not usage:
            continue
        record: Dict[str, Any] = {
            "role": role,
            "model": model_name,
            "token_usage": copy.deepcopy(usage),
        }
        if agent_name:
            record["agent_name"] = agent_name
        records.append(record)
    return records
