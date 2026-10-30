"""Read-only, aggregate-only readiness audit. No guest, message, or document reads."""
import argparse
import json
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import create_engine, text


def audit(engine, *, as_of, environment):
    if engine.dialect.name != 'postgresql':
        raise ValueError('The audit requires PostgreSQL.')
    start = as_of.replace(year=as_of.year - 1, day=1)
    month_index = as_of.year * 12 + as_of.month - 1 + 4
    end = date(month_index // 12, month_index % 12 + 1, 1)
    report = {'environment': environment, 'generated_at': datetime.now(timezone.utc).isoformat(),
              'as_of': as_of.isoformat(), 'coverage_start': start.isoformat(),
              'coverage_end_exclusive': end.isoformat(), 'currency': 'USD',
              'limitations': ['Counts describe this database only, not verified Hostaway source truth.',
                  'Field presence does not establish accommodation revenue semantics.',
                  'Listing and portfolio mappings do not prove physical-unit identity or owner entitlement.',
                  'Current reservation rows cannot reconstruct historical booking pace.']}
    with engine.connect() as conn, conn.begin():
        conn.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        tables = set(conn.execute(text("SELECT table_schema || '.' || table_name FROM information_schema.tables")).scalars())

        def rows(sql):
            return [dict(row) for row in conn.execute(text(sql), {'start': start, 'end': end, 'as_of': as_of}).mappings()]

        if 'public.listings' in tables:
            report['listings'] = rows('''SELECT count(*) AS total,
                count(*) FILTER (WHERE currency IS DISTINCT FROM 'USD') AS non_usd_or_unknown,
                count(*) FILTER (WHERE nullif(trim(currency),'') IS NULL) AS missing_currency,
                min(last_synced_at) AS oldest_sync, max(last_synced_at) AS newest_sync
                FROM public.listings''')[0]
            zones = rows('SELECT timezone_name, count(*) AS count FROM public.listings GROUP BY timezone_name')
            invalid = 0
            for zone in zones:
                try:
                    ZoneInfo(zone['timezone_name'] or '')
                except (ValueError, ZoneInfoNotFoundError):
                    invalid += zone['count']
            report['listings']['invalid_or_missing_timezone'] = invalid
            report['listings']['shared_address_groups_to_review'] = rows('''SELECT count(*) AS count FROM (
                SELECT lower(trim(address)), lower(trim(zipcode)) FROM public.listings
                WHERE nullif(trim(address), '') IS NOT NULL
                GROUP BY 1,2 HAVING count(*) > 1) AS candidates''')[0]['count']
        if 'public.reservations' in tables:
            report['reservations'] = rows('''SELECT count(*) AS total,
                min(arrival_date) AS first_arrival, max(departure_date) AS last_departure,
                max(last_synced_at) AS newest_sync,
                count(*) FILTER (WHERE arrival_date IS NULL OR departure_date IS NULL
                    OR departure_date <= arrival_date) AS invalid_dates,
                count(*) FILTER (WHERE nights IS DISTINCT FROM departure_date - arrival_date) AS night_count_mismatch
                FROM public.reservations''')[0]
            report['reservation_statuses_in_window'] = rows('''SELECT status, count(*) AS count
                FROM public.reservations WHERE arrival_date < :end AND departure_date > :start GROUP BY status ORDER BY status''')
            report['invalid_dates_by_status'] = rows('''SELECT status, count(*) AS count FROM public.reservations
                WHERE arrival_date IS NULL OR departure_date IS NULL OR departure_date <= arrival_date GROUP BY status ORDER BY status''')
            report['finance_presence_by_channel_in_window'] = rows('''SELECT channel_name, count(*) AS count,
                count(*) FILTER (WHERE total_price IS NULL) AS missing_total,
                count(*) FILTER (WHERE total_price = 0) AS zero_total,
                count(*) FILTER (WHERE tax_amount IS NULL) AS missing_tax,
                count(*) FILTER (WHERE cleaning_fee IS NULL) AS missing_cleaning,
                count(*) FILTER (WHERE security_deposit_fee IS NULL) AS missing_deposit,
                count(*) FILTER (WHERE currency IS DISTINCT FROM 'USD') AS non_usd_or_unknown,
                count(*) FILTER (WHERE total_price < 0 OR total_price::text IN ('NaN','Infinity','-Infinity')
                    OR tax_amount < 0 OR cleaning_fee < 0 OR security_deposit_fee < 0
                    OR tax_amount::text IN ('NaN','Infinity','-Infinity')
                    OR cleaning_fee::text IN ('NaN','Infinity','-Infinity')
                    OR security_deposit_fee::text IN ('NaN','Infinity','-Infinity')) AS suspect_amounts
                FROM public.reservations WHERE arrival_date < :end AND departure_date > :start
                    AND lower(status) IN ('new','modified','confirmed') GROUP BY channel_name ORDER BY channel_name''')
            report['monthly_confirmed_coverage'] = rows('''SELECT date_trunc('month', arrival_date)::date AS month,
                count(*) AS reservations, count(DISTINCT listing_id) AS listings
                FROM public.reservations WHERE arrival_date >= :start AND arrival_date < :end
                AND lower(status) IN ('new','modified','confirmed') GROUP BY 1 ORDER BY 1''')
            report['reservations']['duplicate_channel_reference_groups'] = rows('''SELECT count(*) AS count FROM (
                SELECT channel_id, channel_reservation_id FROM public.reservations
                WHERE nullif(trim(channel_reservation_id),'') IS NOT NULL
                GROUP BY 1,2 HAVING count(*) > 1) AS duplicates''')[0]['count']
        if 'brain.calendar_snapshots' in tables:
            report['calendar'] = rows('''SELECT count(*) AS rows, count(DISTINCT listing_id) AS listings,
                count(DISTINCT snapshot_date) AS snapshot_days, min(snapshot_date) AS earliest_snapshot,
                max(snapshot_date) AS latest_snapshot FROM brain.calendar_snapshots''')[0]
            report['calendar']['latest_per_listing'] = rows('''WITH latest AS (
                SELECT listing_id, max(snapshot_date) AS day FROM brain.calendar_snapshots GROUP BY listing_id)
                SELECT c.listing_id, latest.day AS snapshot_date, min(calendar_date) AS first_date,
                    max(calendar_date) AS last_date,
                    count(*) FILTER (WHERE calendar_date >= :as_of AND calendar_date < :end) AS days_in_forward_window,
                    count(*) FILTER (WHERE is_available IS NULL) AS unknown_availability
                FROM brain.calendar_snapshots c JOIN latest ON c.listing_id=latest.listing_id AND c.snapshot_date=latest.day
                GROUP BY c.listing_id, latest.day ORDER BY c.listing_id''')
        for key, table, sql in (
            ('operating_portfolios', 'brain.portfolio_listings', 'SELECT count(*) AS mappings, count(DISTINCT portfolio_id) AS portfolios, count(DISTINCT listing_id) AS listings FROM brain.portfolio_listings'),
            ('bookkeeping_mappings', 'bookkeeping.bookkeeping_listing_mappings', 'SELECT count(*) AS mappings, count(DISTINCT portfolio_id) AS portfolios, count(DISTINCT listing_id) AS listings FROM bookkeeping.bookkeeping_listing_mappings WHERE is_active'),
            ('approved_bookkeeping', 'bookkeeping.bookkeeping_workspace_revisions', "SELECT count(*) AS revisions, count(DISTINCT period_id) AS periods, max(created_at) AS latest_revision FROM bookkeeping.bookkeeping_workspace_revisions WHERE status='approved'"),
            ('sales_properties', 'sales.properties', 'SELECT count(*) AS properties, count(listing_id) AS explicitly_linked_listings FROM sales.properties WHERE merged_into_id IS NULL'),
            ('owner_accounts', 'owners.accounts', 'SELECT count(*) AS accounts FROM owners.accounts'),
        ):
            report[key] = rows(sql)[0] if table in tables else {'available': False}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True, help='Human-readable source label; never include credentials.')
    parser.add_argument('--as-of', type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument('--output', help='Optional private JSON evidence file.')
    args = parser.parse_args()
    engine = create_engine(os.environ['OWNER_AUDIT_DATABASE_URL'])
    try:
        result = json.dumps(audit(engine, as_of=args.as_of, environment=args.environment), indent=2, default=str)
        if args.output:
            # Reports contain operational metadata; create private evidence files.
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write(result + '\n')
        else:
            print(result)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
