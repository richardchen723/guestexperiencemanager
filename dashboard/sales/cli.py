"""Explicit schema migration and scheduled reminder capture (never sends)."""
import argparse
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from .models import migrate
from .service import SalesService
from .notifications import generate,capture_pending


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('migrate','reminders','capture'))
    parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args()
    url=os.environ.get('DATABASE_URL')
    if not url or not url.startswith(('postgresql://','postgresql+')): parser.error('An explicit PostgreSQL DATABASE_URL is required.')
    if not 1<=args.limit<=1000: parser.error('--limit must be 1–1000.')
    engine=create_engine(url)
    if args.action=='migrate':
        with engine.begin() as connection: migrate(connection)
        print('Sales schema migrated. No feature grants or sample records created.')
    else:
        with Session(engine) as db,db.begin():
            service=SalesService(db,actor='system:sales-reminders',actor_name='Sales reminders',users={})
            generate(service)
            if args.action=='capture': print(f'Captured {capture_pending(service,args.limit)} notices into the shared outbox. No messages sent.')
            else: print('Reminder queue updated. No messages sent.')
    engine.dispose()


if __name__=='__main__': main()
