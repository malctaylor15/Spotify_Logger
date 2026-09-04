#!/usr/bin/env python3
"""One-off repair: drop Metrics_WoW rows written by verification runs.

While realigning the pipeline, Weekly_Analysis.ipynb was executed twice
against the production database to prove it ran end-to-end on the unified
.venv. The notebook appends to Metrics_WoW as a side effect, so those two runs
left rows for a date on which no report was actually produced or sent.

2026-09-04 was a Friday; the weekly report runs on Sundays and the monthly on
the 1st, so no legitimate row exists for that date. Both rows are artifacts.

The matching Songs_by_date_WoW rows need no special handling: they are
byte-identical to the preceding run's rows for the same dates, so
001_report_table_keys.py collapses them as ordinary duplicates.

Idempotent.

    .venv/bin/python migrations/000_remove_verification_rows.py --dry-run
    .venv/bin/python migrations/000_remove_verification_rows.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "listening_history.db"
ARTIFACT_DATE = "2026-09-04"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"No database at {args.db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(args.db)
    con.execute("BEGIN")
    try:
        rows = con.execute(
            'SELECT rowid, "Date", "# of Songs", "Spotify Cost" '
            'FROM Metrics_WoW WHERE "Date" = ?', (ARTIFACT_DATE,)
        ).fetchall()
        if not rows:
            print(f"No Metrics_WoW rows for {ARTIFACT_DATE} - nothing to do.")
            con.rollback()
            return 0

        print(f"Metrics_WoW rows for {ARTIFACT_DATE}:")
        for r in rows:
            print(f"  rowid={r[0]}  songs={r[2]}  cost={r[3]}")

        cur = con.execute('DELETE FROM Metrics_WoW WHERE "Date" = ?', (ARTIFACT_DATE,))
        print(f"\nDeleted {cur.rowcount} row(s).")
        print("Metrics_WoW now:",
              con.execute("SELECT COUNT(*) FROM Metrics_WoW").fetchone()[0], "rows")

        if args.apply:
            con.commit()
            print("Committed.")
        else:
            con.rollback()
            print("Dry run - rolled back. Re-run with --apply to write.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
