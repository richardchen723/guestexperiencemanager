from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import Column,Date,DateTime,Float,Integer,MetaData,String,Text,Table,create_engine,select,update,text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from sync.reservation_tax_repair import APPROVED_SCOPE_SHA256,read_scope,GUARDS,SOURCE_KEYS,apply_plan,build_plan,digest,validate_plan,write_ledger,main

NATIVE=json.loads(Path(__file__).with_name('fixtures').joinpath('synthetic_reservation_financials.json').read_text())
OBSERVED='2026-10-24T06:55:43+00:00'
NOW=datetime(2026,10,24,7,30,tzinfo=timezone.utc)
OP='38cdca45-cc25-4e6a-9b35-987d38a1d3e1'


def db_row(raw):
    r={'reservation_id':raw['id'],'tax_amount':None}
    for col,key in SOURCE_KEYS.items():
        v=raw[key]
        if col in {'updated_on','latest_activity_on'} and v is not None:v=datetime.fromisoformat(v)
        if col in {'arrival_date','departure_date'}:v=datetime.fromisoformat(v).date()
        r[col]=v
    return r


def plan():
    return build_plan(NATIVE,[db_row(r) for r in NATIVE],[r['id'] for r in NATIVE],
        observed_at=OBSERVED,scope_sha256=APPROVED_SCOPE_SHA256,now=NOW)


@pytest.fixture(params=['sqlite','postgres'] if os.environ.get('TAX_REPAIR_TEST_DATABASE_URL') else ['sqlite'])
def database(request):
    if request.param=='postgres':
        url=make_url(os.environ['TAX_REPAIR_TEST_DATABASE_URL'])
        assert url.database=='hostaway_tax_repair_test' and not url.host
        assert str(url.query.get('host','')).startswith('/tmp/hostaway-tax-investigation-')
        engine=create_engine(url)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT current_database()')).scalar()=='hostaway_tax_repair_test'
            assert conn.execute(text('SELECT inet_server_addr()')).scalar() is None
            assert conn.execute(text('SHOW data_directory')).scalar().startswith('/tmp/hostaway-tax-investigation-')
    else:
        engine=create_engine('sqlite:///:memory:')
    meta=MetaData()
    columns=[]
    for k in GUARDS:
        typ=Integer if k in {'reservation_id','listing_id'} else DateTime if k in {'updated_on','latest_activity_on'} else Date if k in {'arrival_date','departure_date'} else Float if k in {'total_price','tax_amount','cleaning_fee','security_deposit_fee'} else String
        columns.append(Column(k,typ,primary_key=k=='reservation_id'))
    table=Table('reservations',meta,*columns,Column('host_note',String))
    Table('sync_logs',meta,Column('sync_id',Integer,primary_key=True),
        Column('sync_type',String),Column('sync_mode',String),Column('status',String),
        Column('records_processed',Integer),Column('records_created',Integer),Column('records_updated',Integer),
        Column('listing_stats',Text),Column('started_at',DateTime),Column('completed_at',DateTime),Column('duration_seconds',Float))
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert(),[{**db_row(r),'host_note':'preserve me'} for r in NATIVE])
    yield engine,table
    meta.drop_all(engine)
    engine.dispose()


def test_synthetic_plan_has_two_tax_updates_and_four_unknowns():
    p=plan();updates={d['reservation_id']:d['proposed_tax'] for d in p['decisions'] if d['action']=='update'}
    assert updates=={10001:'80',10004:'120'}
    assert next(d for d in p['decisions'] if d['reservation_id']==10006)['reason']=='component_total_mismatch'
    validate_plan(p,now=NOW)


def test_apply_is_idempotent_changes_only_tax_and_rollback_restores_null(database):
    engine,table=database;p=plan()
    with engine.begin() as conn:
        before=[dict(r) for r in conn.execute(select(table).order_by(table.c.reservation_id)).mappings()]
        assert apply_plan(conn,p,operation_id=OP,now=NOW)['changed']==2
        assert apply_plan(conn,p,operation_id=OP,now=NOW)['already_applied']==2
        after=[dict(r) for r in conn.execute(select(table).order_by(table.c.reservation_id)).mappings()]
        assert [{k:v for k,v in r.items() if k!='tax_amount'} for r in before]==[{k:v for k,v in r.items() if k!='tax_amount'} for r in after]
    with engine.begin() as conn:
        assert apply_plan(conn,p,operation_id=OP,rollback=True,now=NOW+timedelta(days=3))['changed']==2
        assert apply_plan(conn,p,operation_id=OP,rollback=True,now=NOW+timedelta(days=3))['changed']==0
        assert all(r.tax_amount is None for r in conn.execute(select(table)))


def test_concurrent_source_change_aborts_all_candidates(database):
    engine,table=database;p=plan()
    with engine.begin() as conn:conn.execute(update(table).where(table.c.reservation_id==10004).values(total_price=2000))
    with pytest.raises(ValueError,match='Concurrent|Owned'):
        with engine.begin() as conn:apply_plan(conn,p,operation_id=OP,now=NOW)
    with engine.connect() as conn:assert all(r.tax_amount is None for r in conn.execute(select(table)))


def test_rollback_refuses_to_clobber_a_later_tax_change(database):
    engine,table=database;p=plan()
    with engine.begin() as conn:apply_plan(conn,p,operation_id=OP,now=NOW)
    with engine.begin() as conn:conn.execute(update(table).where(table.c.reservation_id==10001).values(tax_amount=190))
    with pytest.raises(ValueError,match='Concurrent|Owned'):
        with engine.begin() as conn:apply_plan(conn,p,operation_id=OP,rollback=True,now=NOW)


def test_postgres_lock_blocks_a_concurrent_writer(database):
    engine,table=database
    if engine.dialect.name!='postgresql':
        pytest.skip('PostgreSQL lock behavior is exercised by the PostgreSQL variant')
    with engine.begin() as first:
        apply_plan(first,plan(),operation_id=OP,now=NOW)
        with pytest.raises(DBAPIError) as caught:
            with engine.begin() as second:
                second.exec_driver_sql("SET LOCAL lock_timeout='200ms'")
                second.execute(update(table).where(table.c.reservation_id==10001).values(tax_amount=7))
        assert caught.value.orig.pgcode=='55P03'
    with engine.connect() as conn:
        assert conn.execute(select(table.c.tax_amount).where(table.c.reservation_id==10001)).scalar()==80.0


def test_postgres_cli_plan_apply_repeat_and_rollback(database,tmp_path,monkeypatch,capsys):
    engine,table=database
    if engine.dialect.name!='postgresql':
        pytest.skip('PostgreSQL CLI is exercised by the PostgreSQL variant')
    # Only the test boundary replaces the pinned private audit with synthetic IDs.
    # Production read_scope remains byte-identical to the reviewed importer.
    scope=str(tmp_path/'synthetic-scope.csv')
    Path(scope).write_text('reservation_id\n'+'\n'.join(str(r['id']) for r in NATIVE)+'\n')
    monkeypatch.setattr('sync.reservation_tax_repair.read_scope',lambda path:[r['id'] for r in NATIVE])
    monkeypatch.setenv('DATABASE_URL',os.environ['TAX_REPAIR_TEST_DATABASE_URL'])
    evidence=tmp_path/'evidence.json';evidence.write_text(json.dumps(NATIVE))
    output=tmp_path/'plan.json';ledger=tmp_path/'rollback.json'
    main(['plan','--scope-csv',scope,'--evidence',str(evidence),'--observed-at',datetime.now(timezone.utc).isoformat(),'--plan',str(output)])
    assert json.loads(capsys.readouterr().out)['updates']==2
    with engine.connect() as conn:assert all(r.tax_amount is None for r in conn.execute(select(table)))
    common=['--scope-csv',scope,'--plan',str(output),'--ledger',str(ledger)]
    main(['apply',*common]);assert json.loads(capsys.readouterr().out)['changed']==2
    assert json.loads(ledger.read_text())['plan']['decisions'][0]['before']['tax_amount'] is None
    main(['apply',*common]);assert json.loads(capsys.readouterr().out)['already_applied']==2
    main(['rollback',*common]);assert json.loads(capsys.readouterr().out)['changed']==2
    with engine.connect() as conn:assert all(r.tax_amount is None for r in conn.execute(select(table)))


def test_plan_rederives_amounts_even_if_outer_hash_is_recomputed():
    p=plan();p['decisions'][0]['proposed_tax']='999'
    p['plan_sha256']=digest({k:v for k,v in p.items() if k!='plan_sha256'})
    with pytest.raises(ValueError,match='rederived'):validate_plan(p,now=NOW)


def test_scope_and_freshness_are_bounded():
    with pytest.raises(ValueError,match='scope'):
        build_plan(NATIVE,[db_row(r) for r in NATIVE],[1],observed_at=OBSERVED,scope_sha256=APPROVED_SCOPE_SHA256,now=NOW)
    with pytest.raises(ValueError,match='24 hours'):validate_plan(plan(),now=NOW+timedelta(days=2))


def test_known_existing_tax_is_not_overwritten_and_source_drift_is_skipped():
    rows=[db_row(r) for r in NATIVE];rows[0]['tax_amount']=25;rows[3]['updated_on']=datetime(2026,10,1)
    p=build_plan(NATIVE,rows,[r['id'] for r in NATIVE],observed_at=OBSERVED,scope_sha256=APPROVED_SCOPE_SHA256,now=NOW)
    assert not any(d['action']=='update' for d in p['decisions'])
    assert p['decisions'][0]['reason']=='preserve_existing_known_tax'
    assert p['decisions'][3]['reason']=='source_database_mismatch'


def test_backup_is_exclusive_and_contains_original_null(tmp_path):
    path=tmp_path/'rollback.json';p=plan();write_ledger(path,p)
    assert json.loads(path.read_text())['plan']['decisions'][0]['before']['tax_amount'] is None
    assert path.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):write_ledger(path,p)


def test_repair_rejects_an_altered_scope_file_before_database_access(tmp_path):
    p=tmp_path/'scope.csv';p.write_text('reservation_id\n10001\n')
    with pytest.raises(ValueError,match='approved 383-record'):read_scope(p)


def test_rollback_does_not_erase_another_process_correction(database):
    engine,table=database;p=plan()
    with engine.begin() as conn:
        for d in p['decisions']:
            if d['action']=='update':
                conn.execute(update(table).where(table.c.reservation_id==d['reservation_id']).values(tax_amount=float(d['proposed_tax'])))
    with engine.begin() as conn:
        result=apply_plan(conn,p,operation_id=OP,now=NOW)
        assert result['changed']==0 and result['already_applied']==2 and result['owned_ids']==[]
    with engine.begin() as conn:
        result=apply_plan(conn,p,operation_id=OP,rollback=True,now=NOW)
        assert result['changed']==0 and result['owned_ids']==[]
        assert conn.execute(select(table.c.tax_amount).where(table.c.reservation_id==10001)).scalar()==80.0
        assert conn.execute(select(table.c.tax_amount).where(table.c.reservation_id==10004)).scalar()==120.0


def test_pending_ledger_without_committed_audit_never_authorizes_rollback(database):
    engine,_=database
    with pytest.raises(ValueError,match='No committed ownership'):
        with engine.begin() as conn:apply_plan(conn,plan(),operation_id=OP,rollback=True,now=NOW)


def test_failed_transaction_does_not_claim_ownership(database):
    engine,table=database
    with pytest.raises(RuntimeError):
        with engine.begin() as conn:
            apply_plan(conn,plan(),operation_id=OP,now=NOW)
            raise RuntimeError('injected transaction failure')
    with engine.begin() as conn:
        assert all(r.tax_amount is None for r in conn.execute(select(table)))
        assert apply_plan(conn,plan(),operation_id=OP,now=NOW)['changed']==2


def test_second_operation_owns_none_of_first_operation_writes(database):
    engine,table=database;p=plan();second='2054c83f-d392-4176-9d66-1e976c0d0706'
    with engine.begin() as conn:apply_plan(conn,p,operation_id=OP,now=NOW)
    with engine.begin() as conn:
        assert apply_plan(conn,p,operation_id=second,now=NOW)['owned_ids']==[]
    with engine.begin() as conn:
        assert apply_plan(conn,p,operation_id=second,rollback=True,now=NOW)['changed']==0
        assert conn.execute(select(table.c.tax_amount).where(table.c.reservation_id==10001)).scalar()==80.0
    with engine.begin() as conn:assert apply_plan(conn,p,operation_id=OP,rollback=True,now=NOW)['changed']==2


def test_postgres_concurrent_repairs_do_not_share_ownership(database):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from threading import Event
    engine,_=database
    if engine.dialect.name!='postgresql':
        pytest.skip('PostgreSQL concurrency is exercised by the PostgreSQL variant')
    p=plan();started=Event();second='2054c83f-d392-4176-9d66-1e976c0d0706'
    def competing_apply():
        with engine.begin() as conn:
            conn.exec_driver_sql("SET LOCAL lock_timeout='3s'")
            started.set()
            return apply_plan(conn,p,operation_id=second,now=NOW)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as first:
            assert apply_plan(first,p,operation_id=OP,now=NOW)['changed']==2
            future=pool.submit(competing_apply)
            assert started.wait(1)
            with pytest.raises(TimeoutError):future.result(timeout=0.1)
        result=future.result(timeout=4)
    assert result['changed']==0 and result['already_applied']==2 and result['owned_ids']==[]
    with engine.begin() as conn:
        assert apply_plan(conn,p,operation_id=second,rollback=True,now=NOW)['changed']==0
        assert apply_plan(conn,p,operation_id=OP,rollback=True,now=NOW)['changed']==2
