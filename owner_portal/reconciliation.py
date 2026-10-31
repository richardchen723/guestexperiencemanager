"""Use reviewed financial evidence only while its source snapshot still matches."""
import hashlib
import json

from .calculations import number


def source_digest(detail, fields):
    """Hash only identity, dates and finance; never retain guest data or notes."""
    def normalized(value):
        amount = number(value)
        return str(amount.normalize()) if amount is not None else value
    raw = {key: detail.get(key) for key in (
        'id', 'listingMapId', 'arrivalDate', 'departureDate', 'status', 'currency')}
    for key in ('totalPrice', 'channelCommissionAmount'):
        raw[key] = normalized(detail.get(key))
    components = [{key: normalized(field.get(key)) if key == 'total' else field.get(key)
                   for key in ('id', 'name', 'type', 'total', 'currency', 'isDeleted', 'isIncludedInTotalPrice')}
                  for field in fields]
    components.sort(key=lambda field: json.dumps(field, sort_keys=True))
    return hashlib.sha256(json.dumps([raw, components], sort_keys=True).encode()).hexdigest()


def reviewed_amount(review, fingerprint, detail, fields):
    if (review is None or review.revoked_at is not None or review.currency != 'USD'
            or review.fingerprint != fingerprint or not review.evidence
            or not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields)
            or review.source_digest != source_digest(detail, fields)):
        return None
    amount = number(review.accommodation)
    return amount if amount is not None and amount >= 0 else None
