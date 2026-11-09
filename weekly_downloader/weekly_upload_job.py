#!/usr/bin/env python3
"""Process audio uploaded by another machine for the latest completed week."""

from __future__ import annotations

import argparse
import ast
import math
from collections import Counter
from itertools import combinations
import datetime as dt
import difflib
import html
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import analyze_stems
import library_db
import lyrics_analysis

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_DB = REPO_ROOT / "data" / "listening_history.db"
DOWNLOADS_DIR = HERE / "downloads"
REPORTS_DIR = HERE / "reports"
EXCLUDED_PLAYLIST = "spotify:playlist:0mnWqdNjnmb0AqNmXzQ0Vz"


def latest_completed_sunday(today: dt.date | None = None) -> dt.date:
    """Sunday starting the most recent fully completed Sunday-Saturday week."""
    today = today or dt.date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    current_sunday = today - dt.timedelta(days=days_since_sunday)
    return current_sunday - dt.timedelta(days=7)


def top_songs(db_path: Path, week: dt.date, limit: int = 8) -> list[dict]:
    end = week + dt.timedelta(days=6)
    sql = """
    WITH excluded AS (
      SELECT DISTINCT name, artist_name FROM Listening_History
      WHERE playlist_id = ?
    )
    SELECT h.name, h.artist_name, COUNT(*) AS plays,
           ROUND(SUM(h.duration_min), 1) AS minutes,
           ROUND(AVG(h.popularity), 0) AS track_popularity,
           COUNT(DISTINCT h.played_at_date) AS active_days,
           MIN(h.played_at_date) AS first_played,
           MAX(h.played_at_date) AS last_played
    FROM Listening_History h
    LEFT JOIN excluded e ON e.name=h.name AND e.artist_name=h.artist_name
    WHERE e.name IS NULL
      AND (h.playlist_id IS NULL OR h.playlist_id != ?)
      AND date(h.played_at_date) BETWEEN date(?) AND date(?)
    GROUP BY h.name, h.artist_name
    ORDER BY plays DESC, minutes DESC, h.artist_name, h.name
    LIMIT ?
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            sql, (EXCLUDED_PLAYLIST, EXCLUDED_PLAYLIST,
                  week.isoformat(), end.isoformat(), limit))]
        for row in rows:
            artist = conn.execute("""
                SELECT popularity, followers, genres FROM Artists_Info
                WHERE artist_name = ? ORDER BY pull_date DESC LIMIT 1
            """, (row["artist_name"],)).fetchone()
            row["artist_popularity"] = artist[0] if artist else None
            row["followers"] = artist[1] if artist else None
            row["genres"] = artist[2] if artist else None
    return rows


def file_identity(path: Path) -> str:
    artist, title = analyze_stems.split_title(path.stem)
    return library_db.match_key(title, artist)


def inventory(folder: Path, songs: list[dict]) -> tuple[dict[str, Path], list[dict]]:
    paths = list(folder.glob("*.mp3"))
    files = {file_identity(p): p for p in paths}
    parsed_files = [(p, *analyze_stems.split_title(p.stem)) for p in paths]
    matched: dict[str, Path] = {}
    missing = []
    for song in songs:
        key = library_db.match_key(song["name"], song["artist_name"])
        if key in files:
            matched[key] = files[key]
            continue

        # Spotify and download sources frequently spell soundtrack editions
        # differently, e.g. "- From Pocahontas / Soundtrack Version" versus
        # "(Pocahontas Soundtrack Version)".  Only fuzzy-match within the
        # exact same normalised artist, then require strong title overlap.
        wanted_artist = library_db.match_key("", song["artist_name"])
        wanted_title = library_db.match_key(song["name"], "").split("|", 1)[1]
        candidates = []
        for path, artist, title in parsed_files:
            if library_db.match_key("", artist) != wanted_artist:
                continue
            candidate_title = library_db.match_key(title, "").split("|", 1)[1]
            ratio = difflib.SequenceMatcher(None, wanted_title, candidate_title).ratio()
            candidates.append((ratio, path))
        best = max(candidates, default=(0.0, None), key=lambda item: item[0])
        if best[0] >= 0.62:
            matched[key] = best[1]
        else:
            missing.append(song)
    return matched, missing


def stem_bars(result: dict) -> str:
    colors = {"vocals": "#7c3aed", "drums": "#e11d48",
              "bass": "#0284c7", "other": "#16a34a"}
    bars = []
    for stem in result["ranking"]:
        score = float(result["stems"][stem]["score"])
        bars.append(
            f"<div style='margin:5px 0'><span style='display:inline-block;width:58px'>{html.escape(stem)}</span>"
            f"<span style='display:inline-block;background:#e5e7eb;width:240px;height:12px'>"
            f"<span style='display:block;background:{colors.get(stem, '#555')};width:{score:.1f}%;height:12px'></span>"
            f"</span> <b>{score:.1f}</b></div>")
    return "".join(bars)


def listening_profile(songs: list[dict], results: list[dict]) -> list[str]:
    """Describe top-song preferences using raw metrics rather than interest scores."""
    total = sum(s["plays"] for s in songs)
    lines = [f"Scope: this week's top {len(songs)} songs ({total} plays), excluding the healing playlist. "
             "Repeat plays and listening days are signals of preference, not ratings of enjoyment."]
    genres = Counter()
    known = 0
    for song in songs:
        raw = song.get("genres")
        if isinstance(raw, str):
            try:
                raw = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                raw = [raw] if raw.strip() else []
        if isinstance(raw, str):
            raw = [raw]
        tags = {g.strip() for g in (raw or []) if isinstance(g, str) and g.strip()}
        if tags:
            known += song["plays"]
            for tag in tags:
                genres[tag] += song["plays"]
    if genres and total:
        lines.append("Most-played artist genres: " + "; ".join(
            f"{g}: {n} plays ({n / total:.0%})" for g, n in genres.most_common(5)) +
            f". Genre coverage: {known}/{total} plays. Artist tags overlap and may not describe each song.")
    else:
        lines.append("Genre data unavailable for these songs.")
    song_map = {library_db.match_key(s["name"], s["artist_name"]): s for s in songs}
    profiles = {}
    for result in results:
        context = result.get("context", {})
        key = library_db.match_key(context.get("name", ""), context.get("artist_name", ""))
        song = song_map.get(key)
        stems = result.get("stems", {})
        if not song or any(stems.get(k, {}).get("rms_db") is None or
                           stems.get(k, {}).get("activity") is None
                           for k in analyze_stems.STEM_ORDER):
            continue
        power = {k: 0.0 if stems[k].get("silent") else 10 ** (stems[k]["rms_db"] / 10)
                 for k in analyze_stems.STEM_ORDER}
        energy = sum(power.values())
        if energy <= 0 or not math.isfinite(energy):
            continue
        shares = {k: v / energy for k, v in power.items()}
        profiles[key] = (song, shares, stems["vocals"]["activity"])
    entries = list(profiles.values())
    covered = sum(s["plays"] for s, _, _ in entries)
    lines.append(f"Audio coverage: {len(entries)}/{len(songs)} songs, {covered}/{total} top-song plays "
                 f"({covered / total:.0%})." if total else "Audio coverage: no listening data.")
    if not covered:
        lines.append("Vocal and bass summaries need analyzed audio; no usable measurements yet.")
        return lines
    averages = {k: sum(s["plays"] * shares[k] for s, shares, _ in entries) / covered
                for k in analyze_stems.STEM_ORDER}
    activity = sum(s["plays"] * a for s, _, a in entries) / covered
    lines.append(f"Your sound, weighted by plays: vocals {averages['vocals']:.0%}, "
                 f"bass {averages['bass']:.0%}, drums {averages['drums']:.0%}, "
                 f"other instruments {averages['other']:.0%} of separated-stem energy. "
                 f"Vocal activity averages {activity:.0%} of track frames (above -50 dBFS).")
    for stem, label in (("vocals", "Most vocal-forward"), ("bass", "Most bass-heavy")):
        song, shares, _ = max(entries, key=lambda e: e[1][stem])
        lines.append(f"{label} among analyzed songs: {song['artist_name']} — {song['name']} "
                     f"({shares[stem]:.0%} stem energy; {song['plays']} plays across {song['active_days']} days).")
    favorite, _, _ = max(entries, key=lambda e: (e[0]["plays"], e[0]["active_days"]))
    lines.append(f"Most replayed analyzed song: {favorite['artist_name']} — {favorite['name']} "
                 f"({favorite['plays']} plays across {favorite['active_days']} days).")
    if len(entries) > 1:
        def distance(pair):
            a, b = pair
            return sum((a[1][k] - b[1][k]) ** 2 for k in analyze_stems.STEM_ORDER) + (a[2] - b[2]) ** 2
        a, b = min(combinations(entries, 2), key=distance)
        lines.append(f"Closest sound profiles: {a[0]['artist_name']} — {a[0]['name']} and "
                     f"{b[0]['artist_name']} — {b[0]['name']}, based on the four energy shares and vocal activity. "
                     "This compares arrangement balance, not melody, lyrics, or vocal timbre.")
    lines.append("Energy shares are estimates from separated stems, not percentages of the original mix. "
                 "Vocal activity can include separation leakage; these measurements do not identify singing style or voice type.")
    return lines


def build_html(songs: list[dict], missing: list[dict], results: list[dict],
               uploaded_keys: set[str], week: str, lyrics: dict | None = None) -> str:
    # Use the Spotify identity attached during inventory/analysis.  Comparing
    # display labels is incorrect for source-file variants such as
    # "wicked game"/"Wicked Game" and soundtrack edition suffixes.
    result_map = {
        library_db.match_key(r["context"]["name"], r["context"]["artist_name"]): r
        for r in results if r.get("context")
    }
    max_plays = max((s["plays"] for s in songs), default=1)
    missing_rows = "".join(
        f"<li><b>#{songs.index(s)+1} {html.escape(s['artist_name'])} — {html.escape(s['name'])}</b> "
        f"({s['plays']} plays, {s['minutes']:.1f} min)</li>" for s in missing
    ) or "<li><b>None — all eight files are available.</b></li>"
    chart_rows = []
    detail_rows = []
    for rank, s in enumerate(songs, 1):
        label = f"{s['artist_name']} - {s['name']}"
        song_key = library_db.match_key(s["name"], s["artist_name"])
        width = 100 * s["plays"] / max_plays
        genres = s.get("genres") or "genre unavailable"
        popularity = (f"track {int(s['track_popularity'])}/100; artist {s['artist_popularity']}/100"
                      if s.get("artist_popularity") is not None else
                      f"track {int(s['track_popularity'])}/100")
        available = "Uploaded" if song_key in uploaded_keys else "Missing"
        chart_rows.append(f"""
          <tr><td style='padding:7px 10px 7px 0'><b>#{rank}</b></td>
          <td style='padding:7px 10px 7px 0'><b>{html.escape(s['name'])}</b><br>
            <span style='color:#555'>{html.escape(s['artist_name'])}</span></td>
          <td style='padding:7px 10px 7px 0'>{s['plays']} plays<br>{s['minutes']:.1f} min</td>
          <td style='padding:7px 10px 7px 0'>{popularity}<br>{s['active_days']} listening day(s)</td>
          <td style='padding:7px 0;color:{'#15803d' if available == 'Uploaded' else '#b91c1c'}'><b>{available}</b></td></tr>
          <tr><td></td><td colspan='4'><div style='height:8px;background:#e5e7eb;max-width:480px'>
            <div style='height:8px;background:#1db954;width:{width:.1f}%'></div></div>
            <span style='color:#777;font-size:12px'>{html.escape(str(genres))}</span></td></tr>""")
        result = result_map.get(song_key)
        if result:
            top = result["ranking"][0]
            metric = result["stems"][top]
            detail_rows.append(f"""
              <div style='border-top:1px solid #ddd;padding:18px 0'>
                <h3 style='margin:0 0 4px'>#{rank} {html.escape(label)}</h3>
                <p style='margin:0 0 10px;color:#555'>{s['plays']} plays · {s['minutes']:.1f} minutes ·
                  Spotify popularity {int(s['track_popularity'])}/100 · heard on {s['active_days']} day(s)</p>
                <p><b>Strongest stem: {html.escape(top)}</b> — {html.escape(analyze_stems.describe(top, metric))}</p>
                {stem_bars(result)}
              </div>""")
    return f"""<html><body style='font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:820px;color:#111'>
      <h2>Weekly uploaded-music report</h2><p style='color:#555'>Week of {week} · Sunday through Saturday</p>
      <h3>1. Songs missing from this week's top 8</h3><ol>{missing_rows}</ol>
      <h3>2. This week's top 8</h3>
      <table style='border-collapse:collapse;width:100%'><tr style='text-align:left;color:#666'>
        <th>Rank</th><th>Song</th><th>Listening</th><th>Context</th><th>File</th></tr>{''.join(chart_rows)}</table>
      <h3>Your listening profile</h3>
      {''.join('<p>' + html.escape(line) + '</p>' for line in listening_profile(songs, results))}
      {lyrics_analysis.render_html(lyrics) if lyrics is not None else ''}
      <h3 style='margin-top:30px'>Uploaded-song analysis</h3>
      <p style='color:#555'>The bars compare Demucs stem-interest scores within each song; they combine prominence, movement, activity, dynamics, and complexity.</p>
      {''.join(detail_rows) if detail_rows else '<p>No uploaded top-8 songs were available to analyze.</p>'}
      <p style='color:#888;font-size:12px'>Generated locally; uploaded audio never leaves this machine.</p>
    </body></html>"""


def build_text(songs: list[dict], missing: list[dict], results: list[dict],
               uploaded_keys: set[str], week: str, lyrics: dict | None = None) -> str:
    lines = [f"Weekly uploaded-music report — week of {week}", "",
             "1. Songs missing from this week's top 8"]
    lines.extend([f"  #{songs.index(s)+1} {s['artist_name']} - {s['name']} ({s['plays']} plays)"
                  for s in missing] or ["  None — all eight files are available."])
    lines += ["", "2. This week's top 8"]
    for rank, s in enumerate(songs, 1):
        label = f"{s['artist_name']} - {s['name']}"
        song_key = library_db.match_key(s["name"], s["artist_name"])
        lines.append(f"  #{rank} {label}: {s['plays']} plays, {s['minutes']:.1f} min, "
                     f"popularity {int(s['track_popularity'])}/100, "
                     f"{s['active_days']} day(s), "
                     f"{'uploaded' if song_key in uploaded_keys else 'missing'}")
    lines += ["", "Your listening profile", *listening_profile(songs, results)]
    if lyrics is not None:
        lines += ["", lyrics_analysis.render_text(lyrics)]
    lines += ["", "Uploaded-song analysis"]
    for result in results:
        top = result["ranking"][0]
        lines.append(f"  {result['track']}: strongest stem {top} "
                     f"({result['stems'][top]['score']:.1f}) — "
                     f"{analyze_stems.describe(top, result['stems'][top])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--week-of", type=dt.date.fromisoformat,
                        default=latest_completed_sunday())
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Reanalyse audio even when stored metrics exist")
    parser.add_argument("--lyrics-cache", type=Path, default=lyrics_analysis.DEFAULT_CACHE,
                        help="Reviewed derived lyric summaries (no raw lyrics)")
    parser.add_argument("--skip-audio", action="store_true",
                        help="Build from stored analyses only; do not separate audio")
    args = parser.parse_args()

    songs = top_songs(args.db, args.week_of, args.top)
    if not songs:
        print(f"No listening history for week of {args.week_of}.")
        return 0
    folder = DOWNLOADS_DIR / f"week_of_{args.week_of.isoformat()}"
    folder.mkdir(parents=True, exist_ok=True)
    matched, missing = inventory(folder, songs)
    print(f"Week of {args.week_of}: {len(matched)}/{len(songs)} top songs uploaded; {len(missing)} missing.")
    for song in missing:
        print(f"  missing: {song['artist_name']} - {song['name']}")

    library = library_db.connect()
    results = []
    stored = {}
    if args.skip_audio:
        for result in library_db.load_week_results(library, args.week_of.isoformat()):
            artist, title = analyze_stems.split_title(result["track"])
            stored[library_db.match_key(title, artist)] = result
    with tempfile.TemporaryDirectory(prefix="demucs_") as tmp:
        for song in songs:
            key = library_db.match_key(song["name"], song["artist_name"])
            track = matched.get(key)
            if args.skip_audio:
                result = stored.get(key)
                if result is None and track:
                    result = stored.get(file_identity(track))
            else:
                if not track:
                    continue
                result = analyze_stems.analyse_track(
                    track, Path(tmp), analyze_stems.DEMUCS_MODEL, False,
                    library=library, week_of=args.week_of.isoformat(), force=args.force)
            if result:
                # Preserve the canonical Spotify identity selected by the
                # inventory matcher; the audio filename may legitimately use
                # different case, punctuation, or edition wording.
                result["context"] = song
                results.append(result)
    library.close()
    uploaded_keys = set(matched)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    week = args.week_of.isoformat()
    lyrics = lyrics_analysis.build_report(songs, week, args.lyrics_cache)
    html_body = build_html(songs, missing, results, uploaded_keys, week, lyrics)
    text_body = build_text(songs, missing, results, uploaded_keys, week, lyrics)
    report = REPORTS_DIR / f"weekly_uploads_{week}.html"
    report.write_text(html_body)
    report.with_suffix(".txt").write_text(text_body)
    (REPORTS_DIR / f"weekly_uploads_{week}.json").write_text(json.dumps({
        "week_of": week, "top_songs": songs, "missing": missing,
        "analyses": results, "lyrics_analysis": lyrics}, indent=2))
    print(f"Report written to {report}")
    if args.send and not args.report_only and not analyze_stems.send_email(
            f"Top 8 music upload report - week of {week}", text_body, html_body):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
