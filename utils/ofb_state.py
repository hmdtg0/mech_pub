"""Grid-state carry for Order from BOM — the typing must survive the rerun.

st.data_editor keeps a user's edits as INDEX-keyed diffs against the frame
the grid was drawn with. That state dies whenever the frame is rebuilt (a
units/ETA change rotates the grid keys) and silently MISALIGNS whenever the
row set shifts underneath it (a part leaving for the open-order filter moves
every diff below it onto the wrong part). So before any rebuild, the state
is lifted out BY PART ID with these helpers, and re-applied through the
page's seed. Pure functions: the page passes session_state in, the tests
pass plain dicts (Hamid, 28 Aug: "it should save its states").
"""
import pandas as pd


def eta_cell(value):
    """An ETA value from editor state, as something a DateColumn can seed.
    Committed-but-unrendered edits arrive as ISO strings."""
    if isinstance(value, str):
        try:
            return pd.to_datetime(value).date()
        except (ValueError, TypeError):
            return None
    return value


def harvest(frames: dict, widget_state) -> dict:
    """{Part ID: row as the user last saw it} across every group.

    Each group's last-drawn rows with its editor's committed diffs merged
    on top — indices arrive as int OR str depending on the Streamlit path,
    so both spellings are honoured. An edit committed instants before the
    caller's click has reached the widget state but not yet a finished run,
    which is exactly why the diffs are read here rather than trusting the
    last returned frame alone.
    """
    out = {}
    for _group, blob in (frames.get("groups", {}) or {}).items():
        state = widget_state.get(blob.get("key", ""))
        edited = state.get("edited_rows", {}) if isinstance(state, dict) else {}
        for i, rec in enumerate(blob.get("rows", [])):
            rec = dict(rec)
            for k in (i, str(i)):
                if k in edited and isinstance(edited[k], dict):
                    rec.update(edited[k])
            rec["ETA"] = eta_cell(rec.get("ETA"))
            pid = str(rec.get("Part ID", "")).strip()
            if pid:
                out[pid] = rec
    return out


def carry_over(harvested: dict, last_units, last_eta) -> dict:
    """The harvest, minus cells still sitting on their §1 defaults.

    When units or the default ETA change, the rows rebuild — cells the user
    never touched must FOLLOW the new defaults (Qty = BOM Qty x units, ETA =
    the section-1 date), while explicit edits must survive the rebuild.
    "Untouched" is read off the values: a Qty equal to BOM Qty x the OLD
    units was the old default, an ETA equal to the OLD default was too.
    A deliberately cleared Qty or ETA is an edit and is kept cleared.
    """
    try:
        last_units = int(last_units)
    except (TypeError, ValueError):
        last_units = None
    out = {}
    for pid, rec in harvested.items():
        rec = dict(rec)
        if last_units is not None:
            try:
                if int(rec.get("Qty")) == int(rec.get("BOM Qty")) * last_units:
                    rec.pop("Qty", None)
            except (TypeError, ValueError):
                pass
        if rec.get("ETA") == last_eta:
            rec.pop("ETA", None)
        out[pid] = rec
    return out
