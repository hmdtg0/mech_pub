"""Process-wide TTL cache for Google Sheet tabs, with write-through patching.

Replaces the per-tab @st.cache_data fetchers. Why a custom cache:

- Write-through: after a Sheets write succeeds we patch the cached rows in
  place, so the post-save st.rerun() renders instantly instead of cold-
  refetching the whole tab (the old .clear() pattern froze the UI 0.5-1s).
- Shared across every session in the process: when one user saves, the others
  see it on their next interaction too.
- No pickle/deep-copy per access (st.cache_data copies on every call; the All
  Orders page reads messages 40+ times per rerun).

Staleness is still bounded by the per-tab TTL exactly as before: patch_rows()
deliberately does NOT refresh the fetch timestamp, so the next TTL-driven
refetch re-reads the sheet and self-corrects any patch that diverged. Worst
case therefore equals the old behavior. If a patch can't resolve a field name
against the cached row's real headers it falls back to invalidating the tab.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_meta_lock = threading.Lock()
# key -> {"value": Any, "fetched": float, "lock": threading.Lock()}
_entries: dict[str, dict] = {}


def _entry(key: str) -> dict:
    e = _entries.get(key)
    if e is None:
        with _meta_lock:
            e = _entries.get(key)
            if e is None:
                # "fetched" is monotonic (immune to clock changes) and drives
                # the TTL; "wall" is the same instant in clock time, for
                # showing the user when the data was read.
                e = {"value": None, "fetched": 0.0, "wall": 0.0,
                     "lock": threading.Lock()}
                _entries[key] = e
    return e


def _copy(val: Any) -> Any:
    # Shallow copy so callers can't mutate the cached container and so a reader
    # iterating never sees a half-applied patch (patches replace the whole list
    # reference). Row dicts are shared and treated read-only by convention.
    if isinstance(val, list):
        return list(val)
    if isinstance(val, dict):
        return dict(val)
    return val


def _load_with_spinner(loader: Callable[[], Any], spinner: str | None) -> Any:
    if not spinner:
        return loader()
    cm = None
    try:
        import streamlit as st
        cm = st.spinner(spinner)
    except Exception:
        cm = None
    if cm is None:
        return loader()
    with cm:
        return loader()


def get(key: str, ttl: float, loader: Callable[[], Any],
        spinner: str | None = None) -> Any:
    """Return the cached value for `key`, calling `loader()` if missing or
    older than `ttl` seconds. Per-key locking prevents a load stampede.
    """
    e = _entry(key)
    now = time.monotonic()
    val = e["value"]
    if val is not None and (now - e["fetched"]) < ttl:
        return _copy(val)
    with e["lock"]:
        # re-check: another thread may have just loaded it
        val = e["value"]
        if val is not None and (time.monotonic() - e["fetched"]) < ttl:
            return _copy(val)
        fresh = _load_with_spinner(loader, spinner)
        e["value"] = fresh
        e["fetched"] = time.monotonic()
        e["wall"] = time.time()
        return _copy(fresh)


def read_at(key: str) -> tuple[float, float] | None:
    """(clock time of the last load, seconds since) or None if not cached.

    When the data was *read* — not when the data itself was produced.
    """
    e = _entries.get(key)
    if e is None or e["value"] is None or not e["fetched"]:
        return None
    return e["wall"], max(0.0, time.monotonic() - e["fetched"])


def invalidate(*keys: str) -> None:
    """Drop cached entries (all if no keys given) so the next get() refetches."""
    if not keys:
        with _meta_lock:
            for e in _entries.values():
                e["value"] = None
                e["fetched"] = 0.0
        return
    for key in keys:
        e = _entries.get(key)
        if e is not None:
            e["value"] = None
            e["fetched"] = 0.0


def _resolve_existing_key(row: dict, field: str) -> str | None:
    """Find the key in `row` that corresponds to `field`, tolerating the sheet
    header drift (trailing spaces, multi-line / misspelled headers). Mirrors
    the startswith/contains rule the sheet handlers use. Returns None if no
    existing key matches.
    """
    if field in row:
        return field
    f = field.strip()
    for k in row:
        if k.strip() == f:
            return k
    for k in row:
        ks = k.strip()
        if ks.startswith(f) or f in ks:
            return k
    return None


def patch_rows(key: str, match: Callable[[dict], bool], updates: dict,
               fuzzy: bool = False) -> int:
    """Copy-on-write replace each row where match(row) is True with a merged
    copy carrying `updates`. Does NOT refresh the fetch timestamp.

    fuzzy=False: set the update keys verbatim (use when the update keys are the
        sheet's real headers, e.g. Orders — keys are authoritative and may add
        a field that was absent from a trimmed row).
    fuzzy=True: resolve each update key to an EXISTING row key; if any can't be
        resolved, invalidate the tab instead of risking a divergent duplicate
        key (use for Delivery / Components, whose headers drift from config).

    Returns the number of rows patched (0 if the tab isn't cached or a fuzzy
    miss triggered invalidation).
    """
    e = _entries.get(key)
    if e is None or not isinstance(e.get("value"), list):
        return 0
    with e["lock"]:
        rows = e["value"]
        if not isinstance(rows, list):
            return 0
        new_rows = list(rows)
        n = 0
        for i, row in enumerate(new_rows):
            if not (isinstance(row, dict) and match(row)):
                continue
            merged = dict(row)
            for field, value in updates.items():
                if fuzzy:
                    rk = _resolve_existing_key(row, field)
                    if rk is None:
                        # header drift we can't map -> refetch instead of guess
                        e["value"] = None
                        e["fetched"] = 0.0
                        return 0
                    merged[rk] = value
                else:
                    merged[field] = value
            new_rows[i] = merged
            n += 1
        e["value"] = new_rows  # replace reference; timestamp intentionally kept
        return n


def insert_row(key: str, row: dict, top: bool = True) -> None:
    """Insert a row dict into a cached list tab (no-op if the tab isn't cached).
    top=True for the insert-at-row-2/3 tabs (newest first); False to append.
    """
    e = _entries.get(key)
    if e is None or not isinstance(e.get("value"), list):
        return
    with e["lock"]:
        rows = e["value"]
        if not isinstance(rows, list):
            return
        new_rows = list(rows)
        if top:
            new_rows.insert(0, row)
        else:
            new_rows.append(row)
        e["value"] = new_rows
