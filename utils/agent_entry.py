"""What a person — or an agent working with one — pastes instead of clicking.

Two shapes, because the two order pages ask different questions:

    parse_lines()   Order from BOM. One line per part, from a BOM that
                    already holds the identity, material and spec.
    parse_fields()  Submit Order. One order, but every field of it, because
                    nothing upstream knows the part yet.

Both refuse rather than guess. A value that does not match the form's own
options comes back as a sentence naming what would have been accepted — the
grids and selectboxes are the guard rails, and text entry does not get to
drive around them (Hamid, 19 Aug: "agentic entry for claude co-work, current
one is difficult").
"""
import re
from datetime import datetime


def parse_lines(text, known_codes, open_codes, default_recipient=""):
    """Order lines as text — Agent Entry on Order from BOM.

    The grids are precise but slow to drive: forty checkboxes and cell edits
    for what someone already has as a list (Hamid, 19 Aug: "agentic entry for
    claude co-work, current one is difficult"). One line per order, fields
    comma/pipe/tab-separated, only the part is required:

        M105
        M105 x120
        M105, 120, Ryan Wong, 25 Aug 2026, urgent, note text...
        # comments and blank lines are ignored

    Returns (rows, errors): rows as {code, qty, recipient, eta, priority,
    notes} with qty=None meaning "keep the grid's default", and errors as
    human sentences. A part with an OPEN order is an error here for the same
    reason it is unselectable in the grids — a second raise for something
    already on its way is a double count, not an order.
    """
    known = {str(c).strip().lower(): str(c).strip() for c in known_codes}
    open_low = {str(c).strip().lower() for c in open_codes}
    rows, errors = [], []

    def _eta(text_value):
        value = str(text_value or "").strip()
        if not value:
            return None
        for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%d %B %Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return "unreadable"

    for n, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # "M105 x120" / "M105 120" — the two-word shorthand.
        parts = [p.strip() for p in re.split(r"[,|\t]", line)]
        if len(parts) == 1:
            m = re.match(r"^(\S+)\s+x?(\d+)$", line, re.I)
            if m:
                parts = [m.group(1), m.group(2)]
        code = known.get(parts[0].lower())
        if code is None:
            errors.append("line %d: `%s` is not a part in this BOM."
                          % (n, parts[0]))
            continue
        if parts[0].lower() in open_low:
            errors.append("line %d: %s already has an order on its way — "
                          "not selectable until it is delivered or cancelled."
                          % (n, code))
            continue
        qty = None
        if len(parts) > 1 and parts[1]:
            digits = re.sub(r"^x", "", parts[1], flags=re.I)
            if not digits.isdigit() or int(digits) < 1:
                errors.append("line %d: quantity `%s` is not a whole number "
                              "of at least 1." % (n, parts[1]))
                continue
            qty = int(digits)
        eta = _eta(parts[3] if len(parts) > 3 else "")
        if eta == "unreadable":
            errors.append("line %d: could not read the date `%s` — use "
                          "e.g. 25 Aug 2026 or 2026-08-25." % (n, parts[3]))
            continue
        priority = (parts[4].strip().upper() if len(parts) > 4 and
                    parts[4].strip() else "")
        if priority and priority not in ("URGENT", "NORMAL"):
            errors.append("line %d: priority `%s` — use URGENT or Normal."
                          % (n, parts[4]))
            continue
        rows.append({
            "code": code,
            "qty": qty,
            "recipient": (parts[2].strip() if len(parts) > 2 and
                          parts[2].strip() else default_recipient),
            "eta": eta,
            "priority": ("URGENT" if priority == "URGENT" else
                         "Normal" if priority else None),
            "notes": ", ".join(parts[5:]).strip() if len(parts) > 5 else "",
        })
    seen = set()
    for r in rows:
        if r["code"] in seen:
            errors.append("%s appears more than once — one line per part."
                          % r["code"])
        seen.add(r["code"])
    return rows, errors


# --- Submit Order: one order, every field -----------------------------------
# The field a name means. Aliases exist because people (and agents) write the
# label they see, the label the sheet uses, or the short one — and all three
# should land in the same box rather than come back as "unknown field".
FIELDS = {
    "part name": "part_name", "part": "part_name", "name": "part_name",
    "process": "process", "manufacturing process": "process",
    "m-code": "m_code", "mcode": "m_code", "m code": "m_code",
    "part id": "m_code", "partid": "m_code", "code": "m_code",
    "version": "version", "rev": "version",
    "material": "material",
    "finish": "finish", "surface finish": "finish",
    "tolerances": "tolerances", "tolerance": "tolerances",
    "critical dimensions": "tolerances",
    "qty": "quantity", "quantity": "quantity", "units": "quantity",
    "priority": "priority",
    "inspection": "inspection", "dimension check": "inspection",
    "recipient": "recipient", "send to": "recipient", "deliver to": "recipient",
    "reviewer": "reviewer",
    "ordered from": "ordered_from", "from": "ordered_from",
    "origin": "ordered_from",
    "holder": "recipient_holder", "receiving holder": "recipient_holder",
    "receiver": "recipient_holder",
    "location": "recipient_location",
    "receiving location": "recipient_location",
    "destination": "recipient_location",
    "receipt note": "receipt_note",
    "notes": "notes", "note": "notes",
}

# What a filled form needs before Submit will take it. Reported as a note,
# not an error: half an order pasted now and finished by hand is a normal way
# to work, and the form does the real gatekeeping at submit time.
REQUIRED = ("part_name", "process", "material", "recipient", "reviewer")


def _choose(value, options, label, line):
    """Match a typed value to one of the form's options, or say why not.

    Exact first, then a unique substring — "cnc" is CNC Machining and there
    is only one. Two matches is an error, not a coin toss.
    """
    options = [str(o) for o in options]
    text = str(value).strip()
    for option in options:
        if option.lower() == text.lower():
            return option, ""
    hits = [o for o in options if text.lower() in o.lower()]
    if len(hits) == 1:
        return hits[0], ""
    if len(hits) > 1:
        return "", ("line %d: %s `%s` matches %s — say which."
                    % (line, label, value, " and ".join("`%s`" % h for h in hits)))
    return "", ("line %d: %s `%s` is not one of: %s."
                % (line, label, value, ", ".join(options) or "(none set up yet)"))


def parse_fields(text, processes=(), reviewers=(), locations=(), holders=()):
    """One order as `field: value` lines — Agent Entry on Submit Order.

        part name: EZ1 Housing revC
        process: CNC
        m-code: M107
        qty: 120
        recipient: Send all to the UK office
        reviewer: Ryan Wong
        # blank lines and comments are ignored

    Returns (values, errors, missing): `values` keyed as the form's own
    prefill dict, `errors` as human sentences, `missing` naming the required
    fields still empty. Anything the form offers as a dropdown is checked
    against that dropdown here, so a pasted order cannot set a holder or a
    reviewer the app does not know.
    """
    values, errors, seen = {}, [], {}
    for n, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([^:=]+)[:=](.*)$", line)
        if not match:
            errors.append("line %d: expected `field: value` — got `%s`."
                          % (n, line))
            continue
        label = match.group(1).strip().lower().rstrip("*").strip()
        value = match.group(2).strip()
        field = FIELDS.get(label)
        if field is None:
            errors.append("line %d: `%s` is not a field on this form. Try: %s."
                          % (n, match.group(1).strip(),
                             ", ".join(sorted({v.replace("_", " ")
                                               for v in FIELDS.values()}))))
            continue
        if field in seen:
            errors.append("line %d: %s was already set on line %d — one line "
                          "each." % (n, field.replace("_", " "), seen[field]))
            continue
        seen[field] = n
        if not value:
            continue

        if field == "quantity":
            digits = re.sub(r"[^0-9]", "", value)
            if not digits or int(digits) < 1:
                errors.append("line %d: quantity `%s` is not a whole number "
                              "of at least 1." % (n, value))
                continue
            values[field] = int(digits)
        elif field == "priority":
            if value.strip().lower() not in ("normal", "urgent"):
                errors.append("line %d: priority `%s` — use URGENT or Normal."
                              % (n, value))
                continue
            values[field] = "URGENT" if value.strip().lower() == "urgent" else "Normal"
        elif field == "inspection":
            yes = value.strip().lower() in ("yes", "y", "true", "1")
            no = value.strip().lower() in ("no", "n", "false", "0")
            if not (yes or no):
                errors.append("line %d: inspection `%s` — use Yes or No."
                              % (n, value))
                continue
            values[field] = "Yes" if yes else "No"
        elif field in ("process", "reviewer", "ordered_from",
                       "recipient_holder", "recipient_location"):
            options = {"process": processes, "reviewer": reviewers,
                       "ordered_from": locations,
                       "recipient_holder": holders,
                       "recipient_location": locations}[field]
            chosen, problem = _choose(value, options,
                                      field.replace("_", " "), n)
            if problem:
                errors.append(problem)
                continue
            values[field] = chosen
        else:
            values[field] = value

    missing = [f for f in REQUIRED if not values.get(f)]
    return values, errors, missing
