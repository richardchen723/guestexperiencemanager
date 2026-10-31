"""Fixed-label property care summaries. No ticket titles, notes or guest data."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .calculations import month_shift

CARE_VIEW_SQL = '''CREATE OR REPLACE VIEW owners.care_source AS
WITH scoped AS (
    SELECT DISTINCT m.property_id,t.ticket_id,t.category,t.status,t.created_at,t.updated_at
    FROM tickets.tickets t JOIN owners.listing_mappings m ON
        (t.listing_id=m.listing_id OR EXISTS(SELECT 1 FROM tickets.ticket_listings tl
         WHERE tl.ticket_id=t.ticket_id AND tl.listing_id=m.listing_id))
    WHERE t.category IN ('cleaning','maintenance') AND coalesce(t.ticket_type,'standard')='standard'
), changes AS (
    SELECT s.property_id,s.ticket_id,s.category,l.created_at,
        l.activity_metadata::jsonb->>'old_status' old_status,
        l.activity_metadata::jsonb->>'new_status' new_status
    FROM scoped s JOIN tickets.activity_logs l ON l.entity_id=s.ticket_id
    WHERE l.entity_type='ticket' AND l.action='status_change'
)
SELECT property_id,ticket_id work_id,category,new_status status,created_at happened_at,'transition'::text kind
FROM changes WHERE new_status IN ('Open','Assigned','In Progress','Blocked','Resolved','Closed')
UNION ALL
SELECT s.property_id,s.ticket_id,s.category,
    coalesce((SELECT c.old_status FROM changes c WHERE c.property_id=s.property_id AND c.ticket_id=s.ticket_id ORDER BY c.created_at LIMIT 1),
        CASE WHEN s.created_at=s.updated_at THEN s.status ELSE 'Unknown' END),s.created_at,'created'
FROM scoped s
UNION ALL
SELECT property_id,ticket_id,category,status,updated_at,'current' FROM scoped;
'''


def prepare_care(rows, month, today, timezone_name):
    if month > today:
        return {'available': True, 'future': True, 'completed': 0, 'open': 0, 'items': []}
    local = ZoneInfo(timezone_name)
    start = datetime.combine(month, time.min, local)
    end = datetime.combine(month_shift(month, 1), time.min, local)
    completed, latest = {}, {}
    for row in sorted(rows, key=lambda r: r['happened_at']):
        when = row['happened_at'].replace(tzinfo=timezone.utc).astimezone(local) if row['happened_at'].tzinfo is None else row['happened_at'].astimezone(local)
        if when >= end or when.date() > today:
            continue
        latest[row['work_id']] = row
        # Current status timestamps may reflect edits. Only a recorded status
        # transition is evidence that work was completed in this month.
        if start <= when < end and row['kind']=='transition' and row['status'] in ('Resolved','Closed'):
            completed[row['work_id']] = {'category':row['category'], 'status':'completed', 'date':when.date().isoformat()}
    pending = [{'category':r['category'], 'status':'in_progress' if r['status']=='In Progress' else 'scheduled' if r['status']=='Assigned' else 'open',
                'date':None} for r in latest.values() if r['status'] in ('Open','Assigned','In Progress','Blocked')]
    # Bucket pending work instead of serializing internal IDs or free text.
    groups = {}
    for row in list(completed.values()) + pending:
        key = (row['category'],row['status'],row['date'])
        groups[key] = groups.get(key,0)+1
    items = [dict(category=k[0],status=k[1],date=k[2],count=n) for k,n in groups.items()]
    return {'available':True, 'future':False, 'completed':len(completed), 'open':len(pending), 'items':items}


def public_care(value):
    if not isinstance(value,dict) or value.get('available') is not True:
        return {'available':False, 'items':[]}
    items = []
    for row in value.get('items',[]):
        if row.get('category') not in ('cleaning','maintenance') or row.get('status') not in ('completed','in_progress','scheduled','open'):
            continue
        when = row.get('date')
        if when is not None:
            from datetime import date
            try: when = date.fromisoformat(when).isoformat()
            except (ValueError,TypeError): continue
        if type(row.get('count')) is int and 0 < row['count'] < 100000:
            items.append({k:row[k] for k in ('category','status','count')} | {'date':when})
    return {'available':True, 'future':value.get('future') is True,
            'completed':sum(r['count'] for r in items if r['status']=='completed'),
            'open':sum(r['count'] for r in items if r['status']!='completed'), 'items':items}
