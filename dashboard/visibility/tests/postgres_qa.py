"""Real PostgreSQL migration/concurrency test in a disposable localhost cluster."""
import getpass
import shutil
import socket
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, select, func
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from dashboard.visibility.models import migrate, Report
from dashboard.visibility.service import save
from dashboard.visibility.tests.test_reports import fixture_report


def main():
    pg_ctl = shutil.which("pg_ctl") or "/opt/homebrew/opt/postgresql@14/bin/pg_ctl"
    initdb = str(Path(pg_ctl).with_name("initdb"))
    with tempfile.TemporaryDirectory(prefix="visibility-pg-", dir="/tmp") as tmp:
        root = Path(tmp); cluster = root / "db"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        subprocess.run([initdb,"-D",str(cluster),"-A","trust","--no-locale","-E","UTF8"],check=True,capture_output=True)
        subprocess.run([pg_ctl,"-D",str(cluster),"-l",str(root/"postgres.log"),"-o",f"-F -p {port} -h 127.0.0.1 -k {tmp}","-w","start"],check=True,capture_output=True)
        engine=None
        try:
            engine=create_engine(URL.create("postgresql+psycopg2",username=getpass.getuser(),host="127.0.0.1",port=port,database="postgres"))
            migrate(engine);migrate(engine)
            sessions=sessionmaker(bind=engine)
            def publish(_):
                with sessions() as db:
                    result=save(db,fixture_report(),"same-command",0,"qa");db.commit();return result
            with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(publish,range(2)))
            assert {r['replayed'] for r in results} == {True,False}
            assert results[0]['id']==results[1]['id']
            with sessions() as db:
                assert db.scalar(select(func.count()).select_from(Report))==1
            print("PostgreSQL: repeatable migration, concurrent idempotent publishing and persisted read passed.")
        finally:
            if engine: engine.dispose()
            subprocess.run([pg_ctl,"-D",str(cluster),"-m","fast","-w","stop"],check=True,capture_output=True)


if __name__=="__main__":main()
