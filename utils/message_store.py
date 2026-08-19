"""Order-level messaging via the CENTRAL database sheet's Messages tab.

Messages join orders by OrderID; since orders are central, so are messages.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import gspread

from config import CENTRAL_SHEET_ID
from utils import data_cache, settings
from utils.google_client import with_worksheet

TAB_MESSAGES = "Messages"
# TTL is a runtime setting (Status page), not a constant.


def _cache_key() -> str:
    # One central sheet, one cache — shared across every project.
    return f"{CENTRAL_SHEET_ID}:messages"
_HEADERS = ["MessageID", "OrderID", "Timestamp", "Author", "Content"]


def _create_messages_ws(ss: gspread.Spreadsheet) -> gspread.Worksheet:
    ws = ss.add_worksheet(title=TAB_MESSAGES, rows=2000, cols=5)
    ws.update(values=[["MessageID", "OrderID", "Timestamp", "Author", "Content"]], range_name="A1:E1")
    return ws


def _on_messages_ws(fn):
    return with_worksheet(TAB_MESSAGES, fn, create=_create_messages_ws,
                          sheet_id=CENTRAL_SHEET_ID)


def _load_messages() -> list[dict]:
    all_values = _on_messages_ws(lambda ws: ws.get_all_values())
    if not all_values or len(all_values) < 2:
        return []
    headers = all_values[0]
    return [
        {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
        for row in all_values[1:]
        if any(cell.strip() for cell in row)
    ]


def fetch_all_messages() -> list[dict]:
    """All messages (cached 30s, write-through)."""
    return data_cache.get(_cache_key(), settings.messages_ttl(), _load_messages)


def fetch_messages_for_order(order_id: str) -> list[dict]:
    return [m for m in fetch_all_messages() if m.get("OrderID") == order_id]


def fetch_unread_messages(user_name: str) -> list[dict]:
    """Get messages NOT authored by this user (i.e. messages from others on their orders)."""
    return [m for m in fetch_all_messages() if m.get("Author", "") != user_name]


def send_message(client: gspread.Client, order_id: str, author: str, content: str):
    msg_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # append_row appends after the last data row in one API round trip
    _on_messages_ws(lambda ws: ws.append_row(
        [msg_id, order_id, now, author, content], value_input_option="USER_ENTERED"))
    # write-through: append to the end (messages are chronological)
    data_cache.insert_row(_cache_key(),
                          dict(zip(_HEADERS, [msg_id, order_id, now, author, content])),
                          top=False)
