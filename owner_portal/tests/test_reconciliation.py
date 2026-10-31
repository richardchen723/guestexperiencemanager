from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import text

from owner_portal.calculations import accommodation_amount
from owner_portal.models import FinanceCache, FinanceReconciliation, PerformanceMonth
from owner_portal.prepare import fingerprint, refresh
from owner_portal.reconciliation import reviewed_amount, source_digest
from .test_prepare import sources


def component(id, name, kind, value, included=1, deleted=0):
    return dict(id=id, name=name, type=kind, total=value,
                isIncludedInTotalPrice=included, isDeleted=deleted)


def test_reservation_commission_reconciles_partner_net_without_reducing_room_revenue():
    fields = [component(1, 'baseRate', 'accommodation', 900),
              component(2, 'cleaningFee', 'fee', 100), component(3, 'lodgingTax', 'tax', 100)]
    raw = dict(currency='USD', totalPrice=950, channelCommissionAmount=150)
    assert accommodation_amount(raw, fields) == (Decimal('900'), None)
    assert accommodation_amount(raw | {'channelCommissionAmount': None}, fields)[0] is None
    assert accommodation_amount(raw | {'channelCommissionAmount': 149}, fields)[0] is None
    # The same commission at both API levels is one fee, not two deductions.
    fields.append(component(4, 'hostChannelFee', 'commissions', 150, included=0))
    assert accommodation_amount(raw, fields) == (Decimal('900'), None)
    assert accommodation_amount(raw | {'totalPrice': 800}, fields)[0] is None
    fields[-1]['total'] = 100
    assert accommodation_amount(raw, fields)[0] is None


def test_deleted_room_or_imported_zero_requires_evidence_not_payment_or_old_charges():
    raw = dict(currency='USD', totalPrice=0)
    assert accommodation_amount(raw, [component(1, 'baseRate', 'accommodation', 180, deleted=1)])[0] is None
    assert accommodation_amount(raw, [component(1, 'totalPaid', 'totals', 1200, included=0)])[0] is None


def test_review_is_bound_to_financial_snapshot_and_rejects_changes_and_revocation():
    raw = dict(id=1, listingMapId=101, arrivalDate='2023-03-01', departureDate='2023-03-04',
               status='new', currency='USD', totalPrice=0)
    fields = [component(1, 'totalPaid', 'totals', 1200, included=0)]
    review = SimpleNamespace(fingerprint='source-row', source_digest=source_digest(raw, fields),
        accommodation=Decimal('1000'), currency='USD', evidence=[{'reference': 'reviewed-source'}], revoked_at=None)
    assert reviewed_amount(review, 'source-row', raw, fields) == Decimal('1000')
    assert source_digest(raw | {'guestName': 'PRIVATE', 'hostNote': 'PRIVATE'}, fields) == review.source_digest
    assert source_digest(raw | {'totalPrice': 0.0}, fields) == review.source_digest
    for changed in (raw | {'totalPrice': 1}, raw | {'listingMapId': 202}, raw | {'arrivalDate': '2023-03-02'}):
        assert reviewed_amount(review, 'source-row', changed, fields) is None
    assert reviewed_amount(review, 'source-row', raw, [fields[0] | {'total': 1300}]) is None
    assert reviewed_amount(review, 'changed-row', raw, fields) is None
    assert reviewed_amount(review, 'source-row', raw, None) is None
    review.revoked_at = datetime.now(timezone.utc)
    assert reviewed_amount(review, 'source-row', raw, fields) is None


def test_worker_preserves_reviewed_zero_and_invalidates_revoked_evidence(engine, factory, setup, sources):
    now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    detail = dict(id=1, listingMapId=101, arrivalDate='2026-11-01', departureDate='2026-11-03',
                  status='new', currency='USD', totalPrice=0)
    fields = [component(1, 'baseRate', 'accommodation', 200, deleted=1)]
    class Client:
        calls = 0
        def get_reservation_detail(self, id):
            self.calls += 1
            return detail | {'financeField': fields, 'guestName': 'PRIVATE GUEST'}
        def get_calendar(self, id, start, end):
            return []
    client = Client()
    with factory.begin() as db:
        db.execute(text('UPDATE public.reservations SET total_price=0'))
    refresh(engine, client, now=now)
    with factory.begin() as db:
        row = dict(db.execute(text('SELECT * FROM public.reservations WHERE reservation_id=1')).mappings().one())
        assert db.get(FinanceCache, 1).accommodation is None
        db.add(FinanceReconciliation(reservation_id=1, fingerprint=fingerprint(row), source_digest=source_digest(detail, fields),
            accommodation=0, currency='USD', evidence=[{'reference': 'synthetic-audit'}], reviewed_by='staff:qa', reviewed_at=now+timedelta(seconds=1)))
    refresh(engine, client, now=now+timedelta(seconds=2))
    with factory() as db:
        cache = db.get(FinanceCache, 1)
        assert cache.accommodation == 0 and cache.reason == 'reviewed_evidence'
        month = db.get(PerformanceMonth, (setup['property'], date(2026, 11, 1)))
        assert month.metrics['revenue'] == 0 and month.metrics['nights'] == 2
        assert month.metrics['missing_revenue_nights'] == 0
        assert 'PRIVATE' not in str(month.metrics) and 'synthetic-audit' not in str(month.metrics)
    calls = client.calls
    refresh(engine, client, now=now+timedelta(seconds=3))
    assert client.calls == calls
    with factory.begin() as db:
        db.get(FinanceReconciliation, 1).revoked_at = now+timedelta(seconds=4)
    refresh(engine, client, now=now+timedelta(seconds=5))
    with factory() as db:
        assert db.get(FinanceCache, 1).accommodation is None
        assert db.get(PerformanceMonth, (setup['property'], date(2026, 11, 1))).metrics['missing_revenue_nights'] == 2


def test_fresh_valid_source_supersedes_reviewed_evidence(engine, factory, setup, sources):
    detail = dict(id=1, listingMapId=101, arrivalDate='2026-11-01', departureDate='2026-11-03',
                  status='new', currency='USD', totalPrice=234)
    fields = [component(1, 'baseRate', 'accommodation', 200), component(2, 'tax', 'tax', 34)]
    with factory.begin() as db:
        row = dict(db.execute(text('SELECT * FROM public.reservations WHERE reservation_id=1')).mappings().one())
        db.add(FinanceReconciliation(reservation_id=1, fingerprint=fingerprint(row), source_digest=source_digest(detail, fields),
            accommodation=999, currency='USD', evidence=[{'reference': 'synthetic-audit'}], reviewed_by='staff:qa'))
    class Client:
        def get_reservation_detail(self, id): return detail | {'financeField': fields}
        def get_calendar(self, id, start, end): return []
    refresh(engine, Client(), now=datetime(2026, 11, 1, 12, tzinfo=timezone.utc))
    with factory() as db:
        assert db.get(FinanceCache, 1).accommodation == Decimal('200')
