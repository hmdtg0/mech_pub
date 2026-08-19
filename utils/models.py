"""Data models for Mech Order Helper."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

from config import MOLDING_PROCESSES


@dataclass
class ChecklistItem:
    """A single checklist item for an order."""
    id: str
    text: str
    done: bool = False
    category: str = "general"  # general, tooling, sourcing


@dataclass
class Order:
    """Parsed mech order from the web form."""
    part_name: str = ""
    m_code: str = ""      # NPD part identity, e.g. M107
    version: str = ""     # Major(CAD).Minor(tooling).Batch, e.g. 2.1.1
    process: str = "CNC Machining"
    material: str = ""
    finish: str = ""
    quantity: int = 1
    priority: str = "Normal"  # Normal / URGENT
    inspection: str = "No"    # dimension check by engineer on arrival
    recipient: str = ""
    engineer: str = ""
    raw_message: str = ""


def generate_checklist(order: Order) -> list[dict]:
    """Generate a dynamic checklist based on the manufacturing process."""
    items = []

    def add(text: str, category: str = "general"):
        items.append(asdict(ChecklistItem(
            id=str(uuid.uuid4())[:8],
            text=text,
            category=category,
        )))

    add("Check CAD files / drawings are complete (STEP + PDF drawing)")
    add("Request vendor quote(s)")
    add("Confirm material & finish availability with vendor")

    if order.process in MOLDING_PROCESSES:
        add("Review DFM feedback from vendor", "tooling")
        add("Approve first-article / T1 samples", "tooling")

    add("Place order with vendor")
    add("Reply ETA on Slack")
    add("Handle vendor technical queries (if any)")
    add("Fill tracking number when shipped", "sourcing")

    if order.inspection == "Yes":
        add("Engineer dimension check on arrival")

    return items
