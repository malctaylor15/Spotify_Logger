#!/usr/bin/env python3
"""Backfill the library database from existing report JSON files.

Analysis runs that predate library_db.py wrote their metrics only to
weekly_downloader/reports/stems_<week>.json. Those files contain every number
the email needs, so they can be imported instead of re-running Demucs.

Usage:
    python3 import_reports.py --dry-run     # show what would be imported
    python3 import_reports.py               # import every report found
    python3 import_reports.py --week 2026-08-11
"""

import argparse
import json
import re
import sys
from pathlib import Path

import library_db

HERE = Path(__file__).resolve().parent
REPORTS_DIR = HERE / "reports"
DOWNLOADS_DIR = HERE / "downloads"

WEEK_RE = re.compile(r"stems_(\d{4}-\d{2}-\d{2})\.json$")


def describe_from_record(_stem: str, m: dict) -> str:
    """Rebuild the human description from stored metrics.

    Mirrors analyze_stems.describe(). Imported here rather than imported from
    that module so this script has no numpy/soundfile dependency -- the whole
    point is to run without the analysis stack installed.
    """
    if m.get("description"):
        return m["description"]
    if not m.get("score"):
        return "essentially absent from this track"
    bits = []
    if m.get("n_loudness", 0) > 0.75:
        bits.append("dominant in the mix")
    elif m.get("n_loudness", 0) < 0.25:
        bits.append("sits low in the mix")
    if m.get("n_variation", 0) > 0.75:
        bits.append("lots of movement")
    if m.get("n_dynamics", 0) > 0.75 and m.get("activity", 0) > 0.5:
        bits.append("wide dynamic range")
    if m.get("n_entropy", 0) > 0.75:
        bits.append("harmonically rich")
    if m.get("activity", 0) > 0.9:
        bits.append("plays throughout")
    elif m.get("activity", 0) < 0.4:
        bits.append(f"only active {m.get('activity', 0) * 100:.0f}% of the track")
    return ", ".join(bits) or "unremarkable on every measure"


def find_audio(track_title: str) -> Path | None:
    """Locate the mp3 for a report entry, if it is still on disk."""
    for candidate in DOWNLOADS_DIR.glob(f"week_of_*/{track_title}.mp3"):
        return candidate
    return None


def import_report(conn, path: Path, week: str, model: str,
                  dry_run: bool) -> tuple[int, int]:
    """Import one report file. Returns (tracks, stems)."""
    try:
        results = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  SKIP {path.name}: {exc}", file=sys.stderr)
        return 0, 0

    tracks = stems = 0
    for entry in results:
        title = entry.get("track", "")
        artist, name = library_db.split_title(title) if hasattr(
            library_db, "split_title") else _split(title)
        ranked = [(stem, entry["stems"][stem])
                  for stem in entry.get("ranking", [])
                  if stem in entry.get("stems", {})]
        if not ranked:
            continue

        print(f"  {title}")
        print(f"      {len(ranked)} stems, top: {ranked[0][0]} "
              f"({ranked[0][1].get('score')})")
        tracks += 1
        stems += len(ranked)

        if dry_run:
            continue

        audio = find_audio(title)
        download_id = library_db.record_download(
            conn, name=name, artist=artist, week_of=week,
            file_path=audio, status="downloaded")
        library_db.record_analysis(
            conn, download_id=download_id, name=name, artist=artist,
            week_of=week, model=model, ranked_stems=ranked,
            describe=describe_from_record)
    return tracks, stems


def _split(title: str) -> tuple[str, str]:
    if " - " in title:
        a, n = title.split(" - ", 1)
        return a.strip(), n.strip()
    return "", title


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--library-db", type=Path, default=None)
    parser.add_argument("--week", help="Only import this week (e.g. 2026-08-11)")
    parser.add_argument("--model", default="htdemucs",
                        help="Model name to record for these results")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    reports = sorted(args.reports_dir.glob("stems_*.json"))
    if args.week:
        reports = [p for p in reports if args.week in p.name]
    if not reports:
        print(f"No report JSON files in {args.reports_dir}", file=sys.stderr)
        return 1

    conn = library_db.connect(args.library_db)
    total_t = total_s = 0
    for path in reports:
        m = WEEK_RE.search(path.name)
        if not m:
            continue
        week = m.group(1)
        print(f"\n{path.name}  (week of {week})")
        t, s = import_report(conn, path, week, args.model, args.dry_run)
        total_t += t
        total_s += s

    if args.dry_run:
        print(f"\nDry run: would import {total_t} track(s), {total_s} stem rows.")
        return 0

    stats = library_db.summary(conn)
    print(f"\nImported {total_t} track(s), {total_s} stem rows.")
    print(f"Library now holds {stats['downloads']} songs across "
          f"{stats['weeks']} week(s), {stats['analysed']} analysed.")
    print("\nRebuild and send the email with:")
    print("  python3 weekly_downloader/analyze_stems.py --from-db --send")
    return 0


if __name__ == "__main__":
    sys.exit(main())
