"""Bounded, evidence-driven tax repair. Default operation is a read-only plan.

No API client, model initialization, historical pagination, or token exchange.
Only tax_amount may be written. Apply/rollback require an explicit plan and an
exclusive-create rollback ledger; every row is locked and rechecked atomically.
"""
import argparse
import csv
from datetime import date, datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import MetaData, Table, and_, create_engine, select, update

from sync.reservation_financials import MONEY_FIELDS, amount, map_financials

MAX_IDS = 383
APPROVED_SCOPE_SHA256 = '66e48b4eba548f79845a9e5955b88d0bc0f9b3f0ffdbdc73f2f99f88a07293be'
GUARDS = ('reservation_id', 'listing_id', 'channel_name', 'source', 'status',
          'arrival_date', 'departure_date', 'currency', 'total_price', 'tax_amount',
          'cleaning_fee', 'security_deposit_fee', 'updated_on', 'latest_activity_on')
SOURCE_KEYS = {'listing_id':'listingMapId', 'channel_name':'channelName', 'source':'source',
               'status':'status', 'arrival_date':'arrivalDate', 'departure_date':'departureDate',
               'currency':'currency', 'total_price':'totalPrice', 'cleaning_fee':'cleaningFee',
               'security_deposit_fee':'securityDepositFee', 'updated_on':'updatedOn',
               'latest_activity_on':'latestActivityOn'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def timestamp(value):
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def normalized(key, value):
    if key in MONEY_FIELDS:
        if value is None:
            return None
        number = amount(value)
        if number is None:
            raise ValueError('Invalid stored finance value: '+key)
        return format(number.normalize(), 'f')
    if key in {'updated_on','latest_activity_on'}:
        return timestamp(value)
    if isinstance(value, (datetime,date)):
        return value.isoformat()
    return value


def snapshot(row):
    if not all(key in row for key in GUARDS):
        raise ValueError('Incomplete database guard projection')
    return {key:normalized(key,row[key]) for key in GUARDS}


def validate_scope(ids):
    if not ids or len(ids)>MAX_IDS or len(set(ids))!=len(ids):
        raise ValueError('Scope requires 1–383 unique reservation IDs')
    if any(type(x) is not int or x<=0 for x in ids):
        raise ValueError('Reservation IDs must be positive integers')


def read_scope(path):
    content=path.read_bytes()
    if hashlib.sha256(content).hexdigest()!=APPROVED_SCOPE_SHA256:
        raise ValueError('Scope CSV does not match the approved 383-record audit')
    ids=[int(r['reservation_id']) for r in csv.DictReader(content.decode().splitlines())]
    validate_scope(ids)
    if len(ids)!=MAX_IDS:
        raise ValueError('Approved scope requires exactly 383 IDs')
    return ids


def build_plan(source_rows, db_rows, allowed_ids, *, observed_at, scope_sha256, now=None):
    validate_scope(allowed_ids)
    source_ids=[r.get('id') for r in source_rows]
    validate_scope(source_ids)
    if not set(source_ids)<=set(allowed_ids):
        raise ValueError('Native evidence exceeds approved affected-ID scope')
    if scope_sha256!=APPROVED_SCOPE_SHA256:
        raise ValueError('A verified scope-file SHA-256 is required')
    observed=datetime.fromisoformat(observed_at.replace('Z','+00:00'))
    now=now or datetime.now(timezone.utc)
    if observed.tzinfo is None or not timedelta(0)<=now-observed<=timedelta(hours=24):
        raise ValueError('Native evidence must be timestamped and at most 24 hours old')
    database={r['reservation_id']:r for r in db_rows}
    if len(database)!=len(db_rows) or not set(database)<=set(source_ids):
        raise ValueError('Unexpected or duplicate database rows')
    decisions=[]
    for raw in source_rows:
        rid=raw['id'];db=database.get(rid)
        decision={'reservation_id':rid,'action':'skip','source':raw}
        decisions.append(decision)
        if db is None:
            decision['reason']='database_row_missing';continue
        try:
            guard=snapshot(db)
        except (ValueError,TypeError):
            decision['reason']='incomplete_or_invalid_database_guard';continue
        decision['before']=guard
        mismatches=[]
        for key,source_key in SOURCE_KEYS.items():
            if source_key not in raw or normalized(key,raw[source_key])!=guard[key]:
                mismatches.append(key)
        if mismatches:
            decision.update(reason='source_database_mismatch',mismatches=mismatches);continue
        if guard['status'] not in {'new','modified'} or guard['currency']!='USD':
            decision['reason']='unsupported_status_or_currency';continue
        mapping=map_financials(raw)
        decision['mapping_states']=mapping.states
        if mapping.component_total is not None:
            decision['included_component_total']=str(mapping.component_total)
        tax=mapping.values.get('tax_amount')
        if tax is None or mapping.tax_basis is None:
            decision['reason']=mapping.reason or 'unknown_tax';continue
        if amount(tax)<0:
            decision['reason']='negative_tax_requires_review';continue
        decision.update(tax_basis=mapping.tax_basis,proposed_tax=normalized('tax_amount',tax))
        if guard['tax_amount'] is not None:
            decision['reason']='already_correct' if guard['tax_amount']==decision['proposed_tax'] else 'preserve_existing_known_tax'
            continue
        decision.update(action='update',reason='verified_missing_tax')
    plan={'schema':1,'observed_at':observed_at,'created_at':now.isoformat(),
          'scope_sha256':scope_sha256,'allowed_ids':allowed_ids,'decisions':decisions}
    plan['plan_sha256']=digest(plan)
    return plan


def validate_plan(plan, *, now=None, allow_stale=False):
    body={k:v for k,v in plan.items() if k!='plan_sha256'}
    if plan.get('schema')!=1 or digest(body)!=plan.get('plan_sha256'):
        raise ValueError('Plan integrity check failed')
    validate_scope(plan['allowed_ids'])
    originals=[]
    for d in plan['decisions']:
        if 'before' not in d:
            continue
        row=dict(d['before'])
        # Guard snapshots serialize money as decimals and timestamps as ISO.
        originals.append(row)
    validation_time=datetime.fromisoformat(plan['created_at']) if allow_stale else now or datetime.now(timezone.utc)
    rebuilt=build_plan([d['source'] for d in plan['decisions']], originals,
        plan['allowed_ids'],observed_at=plan['observed_at'],scope_sha256=plan['scope_sha256'],
        now=validation_time)
    if canonical(rebuilt['decisions'])!=canonical(plan['decisions']):
        raise ValueError('Plan differs from independently rederived source decisions')


def table_for(conn):
    return Table('reservations',MetaData(),schema='public' if conn.dialect.name=='postgresql' else None,autoload_with=conn)


def read_rows(conn, ids, *, lock=False):
    validate_scope(ids)
    table=table_for(conn)
    query=select(*(table.c[k] for k in GUARDS)).where(table.c.reservation_id.in_(ids)).order_by(table.c.reservation_id)
    if lock:
        query=query.with_for_update()
    return table,[dict(r) for r in conn.execute(query).mappings()]


def write_ledger(path, plan):
    # Intent is durable before any write. Ownership itself is committed in the
    # same database transaction as actual tax changes, never inferred from values.
    payload={'schema':2,'operation_id':str(uuid4()),'state':'pending',
             'owned_ids':[],'plan_sha256':plan['plan_sha256'],'plan':plan}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write(canonical(payload)+'\n');f.flush();os.fsync(f.fileno())
    return payload


def audit_table(conn):
    return Table('sync_logs',MetaData(),schema='public' if conn.dialect.name=='postgresql' else None,autoload_with=conn)


def operation_lock(conn, operation_id):
    if str(UUID(operation_id))!=operation_id:
        raise ValueError('Invalid operation ID')
    if conn.dialect.name=='postgresql':
        from sqlalchemy import text
        key=int(hashlib.sha256(operation_id.encode()).hexdigest()[:16],16)
        if key >= 2**63:
            key-=2**64
        conn.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})


def ownership_audit(conn, operation_id, plan_hash, *, rollback=False):
    table=audit_table(conn)
    kind='reservation_tax_repair_rollback' if rollback else 'reservation_tax_repair'
    rows=conn.execute(select(table.c.sync_id,table.c.listing_stats).where(
        table.c.sync_type==kind,table.c.listing_stats.contains(operation_id)).limit(2)).mappings().all()
    if len(rows)>1:
        raise ValueError('Duplicate ownership audit; manual inspection required')
    if not rows:
        return None
    entry=json.loads(rows[0]['listing_stats'])
    if entry.get('operation_id')!=operation_id or entry.get('plan_sha256')!=plan_hash:
        raise ValueError('Ownership audit does not match plan')
    ids=entry.get('owned_ids')
    if not isinstance(ids,list) or len(ids)!=len(set(ids)) or any(type(x) is not int for x in ids):
        raise ValueError('Invalid ownership audit')
    return {**entry,'sync_id':rows[0]['sync_id']}


def record_ownership(conn, operation_id, plan, owned_ids, changed, *, rollback=False):
    table=audit_table(conn);now=datetime.now(timezone.utc).replace(tzinfo=None)
    payload={'schema':1,'operation_id':operation_id,'plan_sha256':plan['plan_sha256'],
             'owned_ids':sorted(owned_ids),'state':'rolled_back' if rollback else 'applied',
             'changes':[{'reservation_id':d['reservation_id'],'before':d['before'],
                         'after_tax':d['proposed_tax']} for d in plan['decisions']
                        if d['reservation_id'] in owned_ids]}
    result=conn.execute(table.insert().values(
        sync_type='reservation_tax_repair_rollback' if rollback else 'reservation_tax_repair',
        sync_mode='bounded',status='success',records_processed=len(owned_ids),
        records_created=0,records_updated=changed,listing_stats=canonical(payload),
        started_at=now,completed_at=now,duration_seconds=0))
    return result.inserted_primary_key[0]


def apply_plan(conn, plan, *, operation_id, rollback=False, now=None):
    """Caller owns transaction, including the durable actual-write ownership audit."""
    validate_plan(plan,now=now,allow_stale=rollback)
    operation_lock(conn,operation_id)
    decisions=[d for d in plan['decisions'] if d['action']=='update']
    owner=ownership_audit(conn,operation_id,plan['plan_sha256'])
    reverted=ownership_audit(conn,operation_id,plan['plan_sha256'],rollback=True)
    candidate_ids={d['reservation_id'] for d in decisions}
    if owner and not set(owner['owned_ids'])<=candidate_ids:
        raise ValueError('Ownership exceeds candidate scope')
    if rollback:
        if owner is None:
            raise ValueError('No committed ownership audit; rollback is not authorized')
        if reverted:
            return {'changed':0,'already_applied':len(owner['owned_ids']),
                    'owned_ids':owner['owned_ids'],'audit_sync_id':reverted['sync_id'],'state':'rolled_back'}
        decisions=[d for d in decisions if d['reservation_id'] in owner['owned_ids']]
    elif reverted:
        raise ValueError('Operation already rolled back; use a new reviewed operation')
    table=None;current={}
    if decisions:
        table,rows=read_rows(conn,[d['reservation_id'] for d in decisions],lock=True)
        current={r['reservation_id']:r for r in rows}
        # A competing operation can commit while row locks are acquired. Read the
        # immutable ownership marker again under the reservation locks.
        owner=ownership_audit(conn,operation_id,plan['plan_sha256'])
    updates=[];already=0
    for d in decisions:
        row=current.get(d['reservation_id'])
        if row is None:
            raise ValueError('Guarded reservation disappeared')
        actual=snapshot(row);before=d['before'];after={**before,'tax_amount':d['proposed_tax']}
        expected,target=(after,before) if rollback else (before,after)
        if owner and not rollback:
            if d['reservation_id'] in owner['owned_ids'] and actual!=after:
                raise ValueError('Owned reservation changed after commit')
            already+=int(actual==after)
            continue
        if actual==target:
            already+=1;continue
        if actual!=expected:
            raise ValueError('Concurrent change: reservation '+str(d['reservation_id']))
        updates.append((d,row,target['tax_amount']))
    if owner and not rollback:
        return {'changed':0,'already_applied':already,'owned_ids':owner['owned_ids'],
                'audit_sync_id':owner['sync_id'],'state':'applied'}
    for d,row,target in updates:
        predicates=[table.c[k].is_(None) if row[k] is None else table.c[k]==row[k] for k in GUARDS]
        result=conn.execute(update(table).where(and_(*predicates)).values(tax_amount=None if target is None else float(target)))
        if result.rowcount!=1:
            raise ValueError('Conditional tax update failed')
    owned_ids=owner['owned_ids'] if rollback else [d['reservation_id'] for d,_,_ in updates]
    audit_id=record_ownership(conn,operation_id,plan,owned_ids,len(updates),rollback=rollback)
    return {'changed':len(updates),'already_applied':already,'owned_ids':sorted(owned_ids),
            'audit_sync_id':audit_id,'state':'rolled_back' if rollback else 'applied'}


def finish_ledger(path, ledger, result):
    # The database ownership row is authoritative if this status-file update is
    # interrupted. Pending intent alone never permits a rollback.
    payload={**ledger,'state':result['state'],'owned_ids':result['owned_ids'],
             'audit_sync_id':result['audit_sync_id']}
    temporary=path.with_name(path.name+'.'+str(uuid4())+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write(canonical(payload)+'\n');f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['plan','apply','rollback'])
    parser.add_argument('--evidence',type=Path)
    parser.add_argument('--scope-csv',type=Path,required=True)
    parser.add_argument('--observed-at')
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--ledger',type=Path)
    args=parser.parse_args(argv)
    if args.mode=='plan' and not all([args.evidence,args.scope_csv,args.observed_at]):
        parser.error('plan requires --evidence, --scope-csv and --observed-at')
    if args.mode!='plan' and not args.ledger:
        parser.error('apply/rollback require --ledger')
    url=os.environ.get('DATABASE_URL')
    if not url:
        parser.error('DATABASE_URL must be supplied by the existing environment')
    options={'options':'-c default_transaction_read_only=on -c statement_timeout=10000','connect_timeout':8} if args.mode=='plan' and url.startswith('postgresql') else {}
    ids=read_scope(args.scope_csv)
    engine=create_engine(url,connect_args=options)
    try:
        if args.mode=='plan':
            sources=json.loads(args.evidence.read_text())
            with engine.connect() as conn:
                _,rows=read_rows(conn,[r['id'] for r in sources])
                plan=build_plan(sources,rows,ids,observed_at=args.observed_at,scope_sha256=APPROVED_SCOPE_SHA256)
            with args.plan.open('x') as f:
                f.write(json.dumps(plan,indent=2)+'\n')
            print(json.dumps({'plan_sha256':plan['plan_sha256'],'updates':sum(d['action']=='update' for d in plan['decisions']),'skips':sum(d['action']=='skip' for d in plan['decisions'])}))
        else:
            plan=json.loads(args.plan.read_text())
            if plan.get('allowed_ids')!=ids:
                raise ValueError('Plan scope differs from the approved scope CSV')
            validate_plan(plan,allow_stale=args.mode=='rollback')
            if args.mode=='apply':
                if args.ledger.exists():
                    ledger=json.loads(args.ledger.read_text())
                    if ledger.get('plan_sha256')!=plan['plan_sha256'] or ledger.get('plan')!=plan:
                        raise ValueError('Existing ledger belongs to another plan')
                else:
                    ledger=write_ledger(args.ledger,plan)
            else:
                ledger=json.loads(args.ledger.read_text())
                if ledger.get('plan')!=plan:
                    raise ValueError('Rollback ledger does not match plan')
            if ledger.get('schema')!=2 or not ledger.get('operation_id'):
                raise ValueError('Ledger has no transaction ownership identity')
            with engine.begin() as conn:
                if conn.dialect.name=='postgresql':
                    conn.exec_driver_sql("SET LOCAL lock_timeout='5s'")
                    conn.exec_driver_sql("SET LOCAL statement_timeout='10s'")
                    conn.exec_driver_sql("SET LOCAL synchronous_commit=on")
                result=apply_plan(conn,plan,operation_id=ledger['operation_id'],rollback=args.mode=='rollback')
                # Verify the final guarded values before transaction commit.
                verification=apply_plan(conn,plan,operation_id=ledger['operation_id'],rollback=args.mode=='rollback')
                if verification['changed']:
                    raise ValueError('Post-write verification failed')
            try:
                finish_ledger(args.ledger,ledger,result)
            except OSError as exc:
                raise RuntimeError('Database transaction committed; recover ledger status from sync_logs for operation '+ledger['operation_id']) from exc
            print(json.dumps(result))
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
