#!/usr/bin/env python3
"""Give the two report tables real keys, so re-running a report upserts.

Weekly_Analysis.ipynb appended to Metrics_WoW and Songs_by_date_WoW on every
run, with no constraint of any kind. Re-running a report, or running the
weekly and monthly jobs on the same day, silently produced duplicate rows.

What this does:

  Metrics_WoW
    - adds a `look_back_days` column, backfilled by inverting `Spotify Cost`
      (the notebook computes it as look_back_days / 30 * 9.99, so the window
      is exactly recoverable: 229 weekly rows and 2 monthly rows here)
    - collapses duplicates, keeping the most recently written row per key
    - UNIQUE (Date, look_back_days), so the weekly and monthly reports for the
      same day coexist instead of colliding

  Songs_by_date_WoW
    - collapses duplicates, keeping the most recently written row per date
    - UNIQUE (played_at_date): one row per calendar day

Idempotent - running it twice is a no-op. Takes a timestamped backup first.

    .venv/bin/python migrations/001_report_table_keys.py --dry-run
    .venv/bin/python migrations/001_report_table_keys.py --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "listening_history.db"

METRICS_INDEX = "idx_metrics_wow_key"
SONGS_INDEX = "idx_songs_by_date_wow_key"


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def index_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def duplicate_count(con: sqlite3.Connection, table: str, keys: list[str]) -> int:
    cols = ", ".join(f'"{k}"' for k in keys)
    row = con.execute(
        f'SELECT COALESCE(SUM(c - 1), 0) FROM '
        f'(SELECT COUNT(*) AS c FROM "{table}" GROUP BY {cols})'
    ).fetchone()
    return int(row[0])


def dedupe(con: sqlite3.Connection, table: str, keys: list[str]) -> int:
    """Delete all but the highest-rowid row within each key group."""
    cols = ", ".join(f'"{k}"' for k in keys)
    cur = con.execute(
        f'DELETE FROM "{table}" WHERE rowid NOT IN '
        f'(SELECT MAX(rowid) FROM "{table}" GROUP BY {cols})'
    )
    return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true",
                        help="Actually write. Without it, reports and rolls back.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Explicit no-op mode (the default).")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"No database at {args.db}", file=sys.stderr)
        return 1

    if args.apply:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = args.db.with_name(f"{args.db.stem}.premigration-{stamp}.bak")
        shutil.copy2(args.db, backup)
        print(f"Backup written to {backup}")

    con = sqlite3.connect(args.db)
    con.execute("BEGIN")
    try:
        # -------------------------------------------------- Metrics_WoW ---
        before = con.execute("SELECT COUNT(*) FROM Metrics_WoW").fetchone()[0]
        print(f"\nMetrics_WoW: {before} rows")

        if "look_back_days" in table_columns(con, "Metrics_WoW"):
            print("  look_back_days column already present")
        else:
            con.execute('ALTER TABLE Metrics_WoW ADD COLUMN look_back_days INTEGER')
            print("  added look_back_days column")

        # The notebook sets Spotify Cost = look_back_days / 30 * 9.99, so the
        # reporting window is exactly recoverable for every historical row.
        con.execute(
            'UPDATE Metrics_WoW SET look_back_days = '
            'CAST(ROUND("Spotify Cost" * 30.0 / 9.99) AS INTEGER) '
            'WHERE look_back_days IS NULL'
        )
        breakdown = con.execute(
            "SELECT look_back_days, COUNT(*) FROM Metrics_WoW "
            "GROUP BY look_back_days ORDER BY look_back_days"
        ).fetchall()
        print(f"  backfilled look_back_days: "
              + ", ".join(f"{d}d={c}" for d, c in breakdown))

        dups = duplicate_count(con, "Metrics_WoW", ["Date", "look_back_days"])
        removed = dedupe(con, "Metrics_WoW", ["Date", "look_back_days"])
        print(f"  duplicate rows removed: {removed} (expected {dups})")

        if index_exists(con, METRICS_INDEX):
            print(f"  {METRICS_INDEX} already exists")
        else:
            con.execute(f'CREATE UNIQUE INDEX {METRICS_INDEX} '
                        f'ON Metrics_WoW("Date", look_back_days)')
            print(f"  created {METRICS_INDEX} UNIQUE(Date, look_back_days)")

        # --------------------------------------------- Songs_by_date_WoW ---
        before = con.execute("SELECT COUNT(*) FROM Songs_by_date_WoW").fetchone()[0]
        print(f"\nSongs_by_date_WoW: {before} rows")

        dups = duplicate_count(con, "Songs_by_date_WoW", ["played_at_date"])
        removed = dedupe(con, "Songs_by_date_WoW", ["played_at_date"])
        print(f"  duplicate rows removed: {removed} (expected {dups})")

        if index_exists(con, SONGS_INDEX):
            print(f"  {SONGS_INDEX} already exists")
        else:
            con.execute(f'CREATE UNIQUE INDEX {SONGS_INDEX} '
                        f'ON Songs_by_date_WoW(played_at_date)')
            print(f"  created {SONGS_INDEX} UNIQUE(played_at_date)")

        m = con.execute("SELECT COUNT(*) FROM Metrics_WoW").fetchone()[0]
        s = con.execute("SELECT COUNT(*) FROM Songs_by_date_WoW").fetchone()[0]
        print(f"\nFinal: Metrics_WoW={m} rows, Songs_by_date_WoW={s} rows")

        if args.apply:
            con.commit()
            print("\nCommitted.")
        else:
            con.rollback()
            print("\nDry run - rolled back. Re-run with --apply to write.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
