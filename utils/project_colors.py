"""A stable colour per project.

16 slots, assigned by the project's position in the registry and cycling on
the 17th. Colour is never the only signal — the project **name** is always
printed beside it, and each slot also carries a distinct emoji token for the
places Streamlit won't render HTML (expander labels, dataframe cells).

The palette was validated with the data-viz validator against the light
surface (#fcfcfb), adjacent-pair mode:

    Lightness band   PASS   all 16 inside L 0.43–0.77
    Chroma floor     PASS   all 16 >= 0.1 (none read as grey)
    CVD separation   WARN   worst adjacent ΔE 6.3 (protan) — in the 6–8 band,
                            which is legal because the name always accompanies
                            the colour (secondary encoding)
    Normal vision    PASS   worst adjacent ΔE 27.6
    Contrast         WARN   7 slots below 3:1 on white — mitigated: colours are
                            used as badge *backgrounds* behind dark text, and
                            every badge is labelled

Honest limit: with 16 colours some non-adjacent pairs are close (worst
all-pairs ΔE 8.6 normal vision, and blue-light/lavender collapse under
deuteranopia). Sixteen mutually unmistakable hues do not exist — that is why
the name, not the colour, is the identifier.
"""
from __future__ import annotations

from typing import Dict, List

# (hex, emoji token, human name). Ordered so neighbours differ in both hue and
# lightness — the first projects registered get the most distinct colours.
PALETTE: List[Dict[str, str]] = [
    {"hex": "#1f5fbf", "glyph": "🔵", "name": "blue"},
    {"hex": "#eda100", "glyph": "🟡", "name": "yellow"},
    {"hex": "#a3196e", "glyph": "🟣", "name": "magenta"},
    {"hex": "#63c9a8", "glyph": "🟩", "name": "mint"},
    {"hex": "#b3231f", "glyph": "🔴", "name": "red"},
    {"hex": "#9085e9", "glyph": "🟪", "name": "lavender"},
    {"hex": "#0b6b2f", "glyph": "🟢", "name": "green"},
    {"hex": "#ef8a8a", "glyph": "🟥", "name": "salmon"},
    {"hex": "#4a3aa7", "glyph": "⚫", "name": "violet"},
    {"hex": "#8fbf3f", "glyph": "🟨", "name": "lime"},
    {"hex": "#b4531b", "glyph": "🟤", "name": "rust"},
    {"hex": "#0f8f6b", "glyph": "⬛", "name": "teal"},
    {"hex": "#7a5c00", "glyph": "🟫", "name": "olive"},
    {"hex": "#d183c9", "glyph": "🟧", "name": "orchid"},
    {"hex": "#3b9ae1", "glyph": "🟦", "name": "sky"},
    {"hex": "#f2703a", "glyph": "🟠", "name": "orange"},
]


def _index(project: str) -> int:
    """Slot for a project: its position in the registry, wrapping at 16.

    Registry order is the order projects were added, so an existing project
    keeps its colour when a new one is registered.
    """
    from utils.project_registry import all_projects

    names = list(all_projects())
    try:
        position = names.index(project)
    except ValueError:
        # Unknown project (e.g. removed from the registry): keep it stable
        # rather than crashing, by hashing the name into a slot.
        position = sum(ord(c) for c in project or "")
    return position % len(PALETTE)


def color_for(project: str) -> str:
    return PALETTE[_index(project)]["hex"]


def glyph_for(project: str) -> str:
    return PALETTE[_index(project)]["glyph"]


def tag(project: str) -> str:
    """Token + name, for the two places that can't take HTML: expander labels
    and dataframe cells.

    (Streamlit's `:colour-background[…]` syntax isn't available at the version
    this app supports — it leaks the colour across the rest of the label — and
    it only offers six hues anyway, so the emoji token carries the identity
    there and the colour chip is used everywhere HTML renders.)
    """
    return "%s %s" % (glyph_for(project), project)


def badge_html(project: str, note: str = "", sheet_id: str = "") -> str:
    """A filled chip — colour as background, dark text on top, name inside.

    `note` rides on the same line in muted type (e.g. the data source), so the
    project and where its data came from read as one statement.

    `sheet_id` appends a link to the sheet the page is actually reading. It is
    the same statement continued — *this* project, from *that* sheet, which is
    one click away — and it saves everyone the hunt through Drive for the file
    a number refers to.
    """
    chip = (
        '<span style="background:%s26;border-left:4px solid %s;'
        'padding:2px 8px;border-radius:4px;font-weight:600;">%s</span>'
        % (color_for(project), color_for(project), project)
    )
    out = chip
    if note:
        out += ('<span style="color:#808495;font-size:0.85em;'
                'padding-left:10px;">%s</span>' % note)
    if sheet_id:
        from utils.project_registry import sheet_url

        url = sheet_url(sheet_id)
        if url:
            out += ('<a href="%s" target="_blank" rel="noopener" '
                    'style="font-size:0.85em;padding-left:10px;">open sheet ↗</a>'
                    % url)
    return out
