"""Conservative fallback for the verified website email format.

Larry may supply structured extraction through intake. This parser never treats
the message as instructions and deliberately leaves natural-language dates unset.
"""

import re
from datetime import date


REFERENCE = re.compile(r"\b(?:EH|LR)-[A-Z0-9]{6,40}\b", re.I)
LABELS = {
    "name": "guest_name", "guest name": "guest_name", "email": "email",
    "phone": "phone", "phone number": "phone", "trip type": "trip_type",
    "property": "property_text", "property they're interested in": "property_text",
    "notes/details": "details", "notes": "details", "details": "details",
    "guests": "guest_count", "adults": "adults", "children": "children",
    "pets": "pets", "requirements": "stay_requirements", "budget": "budget",
    "date flexibility": "date_flexibility", "preferred contact method": "preferred_contact_method",
}


def parse_email(subject, body):
    # Do not extract stale fields from the quoted original when reading a reply.
    fresh = re.split(r"(?m)^On .+wrote:\s*$|^[- ]*Original Message[- ]*\s*$|^>.*$", body)[0]
    fields = {}
    for line in fresh.splitlines():
        label, sep, value = line.partition(":")
        key = LABELS.get(label.strip().lower())
        if sep and key and value.strip():
            fields[key] = value.strip()
    if "property_text" not in fields:
        match = re.search(r"(?im)^I'm interested in (.+?)[.]?\s*$", fresh)
        if match:
            fields["property_text"] = match.group(1)
    for key in ("guest_count", "adults", "children"):
        value = fields.get(key)
        if value:
            if re.fullmatch(r"\d{1,3}", value):
                fields[key] = int(value)
            else:
                del fields[key]
    dates = re.search(r"(?im)^Dates:\s*(.+)$", fresh)
    if dates:
        # Only complete ISO dates or explicit month-name date ranges with a year.
        value = dates.group(1).strip().rstrip(".")
        pair = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*(?:to|–|—| - )\s*(\d{4}-\d{2}-\d{2})", value)
        named = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})\s*[–—-]\s*([A-Za-z]+)?\s*(\d{1,2}),?\s+(\d{4})", value)
        try:
            if pair:
                fields["check_in"], fields["check_out"] = [date.fromisoformat(v).isoformat() for v in pair.groups()]
            elif named:
                from datetime import datetime
                first, day1, second, day2, year = named.groups()
                def named_date(month, day):
                    for fmt in ("%b %d %Y", "%B %d %Y"):
                        try:
                            return datetime.strptime(f"{month} {day} {year}", fmt).date().isoformat()
                        except ValueError:
                            pass
                    raise ValueError()
                fields["check_in"] = named_date(first, day1)
                fields["check_out"] = named_date(second or first, day2)
            else:
                fields["date_flexibility"] = value
        except ValueError:
            fields.pop("check_in", None)
            fields.pop("check_out", None)
    reference = REFERENCE.search(subject) or REFERENCE.search(fresh)
    return {"reference": reference.group(0).upper() if reference else None, "fields": fields,
            "review_reasons": [] if fields else ["Larry extraction needed: no structured fields in this message."]}
