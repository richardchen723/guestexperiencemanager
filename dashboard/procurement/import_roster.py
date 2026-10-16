"""Import a supplied initial roster once; never embed people's details in source.

python -m dashboard.procurement.import_roster --file /private/roster.json --key initial-roster
Add --apply after reviewing the resolved property IDs. Later edits use the UI.
Input is a JSON list of {name, properties: [exact names], property_tags: [tags]}.
Optional listing_ids, phone, and active are supported. No login is created.
"""

import argparse
import hashlib
import json
import unicodedata
from pathlib import Path

from sqlalchemy import select, text

from .models import Command, Staff, get_session
from .service import ProcurementService, WorkflowError, required


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value)).casefold() if c.isalnum())


def resolve_roster(properties, entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
        raise WorkflowError('Provide a roster list with 1–200 people.')
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise WorkflowError('Each person must be an object.')
        name = required(entry.get('name'), 'Housekeeper name', 160)
        if name.casefold() in seen:
            raise WorkflowError(f'Duplicate person in import: {name}.')
        seen.add(name.casefold())
        ids = entry.get('listing_ids', [])
        if not isinstance(ids, list):
            raise WorkflowError('listing_ids must be a list.')
        ids = list(ids)
        for field in ('properties', 'property_tags'):
            values = entry.get(field, [])
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                raise WorkflowError(f'{field} must be a list of names.')
            for value in values:
                matches = [p['id'] for p in properties.values() if (
                    normalized(p['name']) == normalized(value) if field == 'properties'
                    else any(normalized(tag) == normalized(value) for tag in p.get('tags', []))
                )]
                if not matches or field == 'properties' and len(matches) != 1:
                    raise WorkflowError(f'{name}: {value!r} did not match uniquely. Use explicit listing_ids.')
                ids.extend(matches)
        if any(isinstance(i, bool) or not str(i).isdigit() or int(i) not in properties for i in ids):
            raise WorkflowError(f'{name}: an assigned property does not exist.')
        result.append({'name': name, 'listing_ids': sorted({int(i) for i in ids}),
                       'phone': entry.get('phone', ''), 'active': entry.get('active', True)})
    return result


def import_roster(service, entries, key, *, apply=False):
    if not service.can_approve:
        raise WorkflowError('Administrator access is required.', 403)
    key = 'roster-import:' + required(key, 'Import key', 160)
    fingerprint = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    previous = service.session.get(Command, key)
    if previous:
        if previous.fingerprint != fingerprint:
            raise WorkflowError('This import key was used for a different roster.', 409)
        return {**previous.result, 'replayed': True}
    plan = resolve_roster(service.properties, entries)
    existing = {s.name.casefold(): s for s in service.session.scalars(select(Staff))}
    result = {'created': [], 'skipped_existing': [], 'replayed': False}
    for person in plan:
        if person['name'].casefold() in existing:
            result['skipped_existing'].append(person['name'])
            continue
        if apply:
            staff = service.save_staff(person)
            person = {**person, 'id': staff.id}
        result['created'].append(person)
    if apply:
        service.session.add(Command(key=key, fingerprint=fingerprint, result=result))
        service.audit('roster_imported', {'import_key': key, 'created_count': len(result['created']),
                                        'skipped_count': len(result['skipped_existing'])})
        service.session.flush()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from dashboard.auth.models import get_user_by_email
    from dashboard.config import OWNER_EMAIL
    from .routes import reference_data
    user = get_user_by_email(OWNER_EMAIL)
    if not user or not user.is_admin() or not user.is_approved:
        parser.error('The configured approved owner is required for roster setup.')
    properties, users = reference_data()
    entries = json.loads(args.file.read_text())
    with get_session() as db:
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(418227390)'))
        service = ProcurementService(db, actor=f'{user.name or user.email} · roster import',
                                     user_id=user.user_id, can_approve=True, properties=properties, users=users)
        result = import_roster(service, entries, args.key, apply=args.apply)
        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
