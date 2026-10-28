#!/usr/bin/env python3
"""Private operator CLI. Reuses the existing deployment identity and DB config."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.visibility.service import validate, save, latest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "migrate", "publish", "list"])
    parser.add_argument("--file")
    parser.add_argument("--key")
    parser.add_argument("--expected-revision", type=int, default=0)
    args = parser.parse_args()
    if args.command in {"validate", "publish"}:
        if not args.file:
            parser.error("--file is required")
        data = json.loads(Path(args.file).read_text())
        validate(data)
        if args.command == "validate":
            print("Valid report; no database connection or write.")
            return
        if not args.key:
            parser.error("--key is required for idempotent publishing")
    from dashboard import config  # Load the established environment, never print it.
    from database.models import get_engine
    from dashboard.visibility.models import get_session, migrate
    if args.command == "migrate":
        migrate(get_engine(None))
        print("Visibility report table ready. No existing records modified or seeded.")
        return
    db = get_session()
    try:
        if args.command == "publish":
            result = save(db, data, args.key, args.expected_revision, "Larry / authorized scheduled report publisher")
            db.commit()
            print(json.dumps({**result, "url": f"https://yourcottoncandy.com/visibility/reports/{result['id']}"}))
        else:
            reports, total = latest(db)
            print(json.dumps({"total": total, "reports": [{k: r[k] for k in ("id", "week_start", "revision", "status", "title")} for r in reports]}))
    finally:
        db.close()


if __name__ == "__main__":
    main()
