from datetime import date

from sqlalchemy import event, text

from owner_portal.audit import audit


def test_audit_transaction_is_read_only_and_ignores_private_tables(engine):
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        result = audit(engine, as_of=date(2026, 10, 30), environment='Synthetic QA')
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert statements[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert result['coverage_start'] == '2025-10-01' and result['coverage_end_exclusive'] == '2027-02-01'
    assert 'private_guest_fixture' not in '\n'.join(statements)
    assert all(s.startswith(('SET ', 'SELECT ')) for s in statements)
    assert result['sales_properties'] == {'available': False}


def test_audit_unknowns_zeroes_statuses_and_invalid_dates(engine):
    # Transactional synthetic source fixture; no real application tables are touched.
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE public.reservations (reservation_id int, listing_id int, channel_id int,
            channel_reservation_id text, channel_name text, arrival_date date, departure_date date, nights int,
            last_synced_at timestamp, total_price float, tax_amount float, cleaning_fee float,
            security_deposit_fee float, currency text, status text)'''))
        conn.execute(text('''INSERT INTO public.reservations VALUES
            (1,101,1,'synthetic-1','direct','2026-10-31','2026-11-02',2,now(),100,NULL,10,0,'USD','new'),
            (2,101,1,'synthetic-2','direct','2026-11-03','2026-11-04',1,now(),0,0,0,0,'USD','modified'),
            (3,101,1,'synthetic-3','direct','2026-11-03','2026-11-04',1,now(),9000,0,0,0,'USD','cancelled'),
            (4,101,1,'synthetic-4','direct',NULL,NULL,NULL,now(),0,0,0,0,'USD','inquiry')'''))
    try:
        result = audit(engine, as_of=date(2026, 10, 30), environment='Synthetic QA')
        channel = result['finance_presence_by_channel_in_window'][0]
        assert channel['count'] == 2 and channel['missing_tax'] == 1 and channel['zero_total'] == 1
        assert result['invalid_dates_by_status'] == [{'status': 'inquiry', 'count': 1}]
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE public.reservations'))
