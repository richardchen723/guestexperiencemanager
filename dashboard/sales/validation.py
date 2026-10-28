"""Bounded input validation and deterministic local-time handling."""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STAGES = ("New Lead", "Research & Qualification", "Outreach", "Interested / Discovery",
          "Property Assessment", "Proposal & Negotiation", "Agreement Signed", "Onboarding")
OUTCOMES = ("open", "nurture", "lost", "disqualified", "won")
CHANNELS = ("email", "phone", "sms", "whatsapp")
MARKETS = ("Port Angeles / Olympic Peninsula", "Hood Canal / Belfair", "Whidbey Island")


class SalesError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def fields(data, allowed):
    if not isinstance(data, dict):
        raise SalesError("A JSON object is required.")
    unknown = set(data) - set(allowed)
    if unknown:
        raise SalesError("Unsupported fields: " + ", ".join(sorted(unknown)))


def string(value, label, limit=10000, required=False):
    if value is None or value == "":
        if required:
            raise SalesError(f"{label} is required.")
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise SalesError(f"{label} must be text of at most {limit} characters.")
    result = value.strip()
    if required and not result:
        raise SalesError(f"{label} is required.")
    return result or None


def integer(value, label, minimum=1, maximum=2147483647, optional=False):
    if optional and value in (None, ""):
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise SalesError(f"{label} must be an integer from {minimum} to {maximum}.")
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise SalesError(f"{label} must be true or false.")
    return value


def choice(value, options, label):
    if value not in options:
        raise SalesError(f"Choose a valid {label}.")
    return value


def money(value, label, maximum=Decimal("99999999999.99")):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise SalesError(f"{label} must be a nonnegative USD amount.")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > maximum or result.as_tuple().exponent < -2:
            raise InvalidOperation
        return result.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise SalesError(f"{label} must be a nonnegative USD amount with at most two decimal places.") from None


def instant(value, label, required=False):
    if not value and not required:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise SalesError(f"{label} needs an ISO timestamp with its timezone offset.") from None


def local_instant(value, zone, label="Due time", fold=None):
    """Reject nonexistent or ambiguous wall times rather than silently shifting them."""
    try:
        naive = datetime.fromisoformat(value)
        if naive.tzinfo is not None:
            raise ValueError
        tz = ZoneInfo(zone)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise SalesError(f"{label} is not a valid local date/time.") from None
    candidates = {}
    for f in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=f)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) == naive:
            candidates[f] = utc
    unique = set(candidates.values())
    if not unique:
        raise SalesError(f"{label} does not exist because clocks change. Choose another time.")
    if len(unique) > 1 and fold not in (0, 1):
        raise SalesError(f"{label} occurs twice because clocks change. Select the first or second occurrence.")
    return candidates[fold] if fold in candidates else next(iter(unique))


def day(value, label, required=False):
    if not value and not required:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise SalesError(f"{label} must be YYYY-MM-DD.") from None


def url(value, label="Document URL", required=False):
    value = string(value, label, 2000, required)
    if value:
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError
        except ValueError:
            raise SalesError(f"{label} must be an HTTPS link without embedded credentials.") from None
    return value


def email(value):
    value = string(value, "Email", 320)
    if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise SalesError("Enter a valid email address.")
    return value


def normalized(value):
    return re.sub(r"\W+", " ", value or "").strip().casefold()


def phone_key(value):
    value = re.sub(r"\D", "", value or "")
    return value if len(value) >= 7 else None


def command_id(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise SalesError("A valid command_id is required to safely retry this operation.") from None


def serial(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=serial, separators=(",", ":")).encode()).hexdigest()


def jsonable(value):
    return json.loads(json.dumps(value, default=serial))
