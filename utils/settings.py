"""Runtime settings an admin can change without a redeploy.

Kept deliberately tiny: values live in `data/settings.json` next to the
snapshots, are read through here, and fall back to the config/env default when
the file has nothing to say. On Streamlit Cloud that file is ephemeral, so
anything that must survive a restart belongs in an env var — the UI says so.

Everything here is a cache TTL: how many seconds a data set is reused before
the Sheet is read again. There is deliberately no idle setting — an untouched
page runs nothing, so idle time already costs zero API calls.
"""
from __future__ import annotations

import json
import os
from typing import Dict

from config import (MESSAGES_TTL_SECONDS, ORDERS_TTL_SECONDS,
                    TRACKER_TTL_SECONDS, USERS_TTL_SECONDS)

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_STORE = os.path.join(_DATA_DIR, "settings.json")

# Guard rails, in seconds. Below the floor the app would re-read on nearly
# every interaction; above the ceiling data goes stale enough to mislead.
TTL_MIN, TTL_MAX = 5, 900

DEFAULTS: Dict[str, int] = {
    "tracker_ttl": TRACKER_TTL_SECONDS,
    "orders_ttl": ORDERS_TTL_SECONDS,
    "users_ttl": USERS_TTL_SECONDS,
    "messages_ttl": MESSAGES_TTL_SECONDS,
}

_cache: Dict[str, int] = {}
_loaded = False


def _load() -> Dict[str, int]:
    global _loaded, _cache
    if _loaded:
        return _cache
    _loaded = True
    try:
        with open(_STORE, encoding="utf-8") as fh:
            data = json.load(fh)
        _cache = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        _cache = {}
    return _cache


def _clamp(value, fallback: int) -> int:
    try:
        return max(TTL_MIN, min(TTL_MAX, int(value)))
    except (TypeError, ValueError):
        return fallback


def get(key: str) -> int:
    """A TTL in seconds, clamped, falling back to the env/config default."""
    fallback = DEFAULTS.get(key, TRACKER_TTL_SECONDS)
    return _clamp(_load().get(key, fallback), fallback)


def all_ttls() -> Dict[str, int]:
    return {key: get(key) for key in DEFAULTS}


def save(values: Dict[str, int]) -> Dict[str, int]:
    """Persist any subset of the TTLs. Returns every TTL after saving."""
    data = _load()
    for key, value in values.items():
        if key in DEFAULTS:
            data[key] = _clamp(value, DEFAULTS[key])
    if not os.path.isdir(_DATA_DIR):
        os.makedirs(_DATA_DIR)
    with open(_STORE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    return all_ttls()


# Named accessors, so call sites read as English rather than dict lookups.
def tracker_ttl() -> int:
    return get("tracker_ttl")


def orders_ttl() -> int:
    return get("orders_ttl")


def users_ttl() -> int:
    return get("users_ttl")


def messages_ttl() -> int:
    return get("messages_ttl")
