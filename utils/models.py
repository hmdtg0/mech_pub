"""Data models for Mech Order Helper."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Order:
    """Parsed mech order from the web form."""
    part_name: str = ""
    m_code: str = ""      # NPD part identity, e.g. M107
    version: str = ""     # Major(CAD).Minor(tolerance/tooling).Batch, e.g. 2.1.1
    process: str = "CNC Machining"
    material: str = ""
    finish: str = ""
    quantity: int = 1
    priority: str = "Normal"  # Normal / URGENT
    inspection: str = "No"    # dimension check by engineer on arrival
    recipient: str = ""
    engineer: str = ""
    raw_message: str = ""
