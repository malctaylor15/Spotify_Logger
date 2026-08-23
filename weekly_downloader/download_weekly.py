#!/usr/bin/env python3
"""Weekly top-songs downloader for Spotify_Logger.

Reads the listening-history database maintained by the Spotify_Logger repo,
finds the top N most-played songs from the last week (excluding anything that
has ever been played from the healing-music playlist), then uses yt-dlp to
search YouTube for each song's official music video and save the audio as an
mp3 into a dated weekly folder.

This folder lives inside the Spotify_Logger repo but is self-contained: it only
*reads* the repo's database and writes exclusively under weekly_downloader/.

Usage:
    python3 download_weekly.py               # query + download top 10
    python3 download_weekly.py --dry-run     # show what would be downloaded
    python3 download_weekly.py --top 5 --days 14
"""

import argparse
import datetime as dt
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import library_db

HERE = Path(__file__).resolve().parent          # .../Spotify_Logger/weekly_downloader
REPO_ROOT = HERE.parent                          # .../Spotify_Logger
DEFAULT_DB = REPO_ROOT / "data" / "listening_history.db"
DOWNLOADS_DIR = HERE / "downloads"

# Healing-music playlist to exclude (Sat-Chit meditation tracks, etc.)
EXCLUDED_PLAYLIST_IDS = [
    "spotify:playlist:0mnWqdNjnmb0AqNmXzQ0Vz",
]

TOP_SONGS_SQL = """
WITH excluded_songs AS (
    SELECT DISTINCT name, artist_name
    FROM Listening_History
    WHERE playlist_id IN ({placeholders})
)
SELECT
    lh.name,
    lh.artist_name,
    COUNT(*)                        AS plays,
    ROUND(SUM(lh.duration_min), 1)  AS minutes_listened
FROM Listening_History lh
LEFT JOIN excluded_songs ex
       ON ex.name = lh.name AND ex.artist_name = lh.artist_name
WHERE ex.name IS NULL
  AND (lh.playlist_id IS NULL OR lh.playlist_id NOT IN ({placeholders}))
  AND date(lh.played_at_date) >= date('now', ?)
GROUP BY lh.name, lh.artist_name
ORDER BY plays DESC, minutes_listened DESC
LIMIT ?
"""


def get_top_songs(db_path: Path, top_n: int, days: int):
    """Return [(name, artist, plays, minutes), ...] for the last `days` days."""
    placeholders = ",".join("?" for _ in EXCLUDED_PLAYLIST_IDS)
    sql = TOP_SONGS_SQL.format(placeholders=placeholders)
    params = (
        EXCLUDED_PLAYLIST_IDS
        + EXCLUDED_PLAYLIST_IDS
        + [f"-{days} day", top_n]
    )
    with sqlite3.connect(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def sanitize(text: str) -> str:
    """Make a string safe-ish for use in a filename."""
    bad = '/\\:*?"<>|'
    return "".join("_" if ch in bad else ch for ch in text).strip()


# JavaScript runtimes yt-dlp can use to solve YouTube's signature / "n"
# challenges, in yt-dlp's order of preference. Deno is enabled by default;
# node and quickjs must be requested explicitly via --js-runtimes.
JS_RUNTIMES = [
    ("deno", None),          # default, no flag needed
    ("node", "node"),
    ("qjs", "quickjs"),
]


def ytdlp_version() -> str | None:
    """Return the installed yt-dlp version string, or None if unavailable."""
    try:
        out = subprocess.run(["yt-dlp", "--version"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
    except Exception:
        return None


def update_ytdlp() -> bool:
    """Upgrade yt-dlp (with the EJS challenge solver) using this pip."""
    print("Updating yt-dlp and the EJS challenge solver ...")
    # The [default] extra pulls in yt-dlp-ejs, which is required to solve
    # YouTube's signature and "n" challenges.
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp[default]"]
    if subprocess.run(cmd).returncode != 0:
        print("pip upgrade failed. If yt-dlp came from Homebrew, run: "
              "brew upgrade yt-dlp", file=sys.stderr)
        return False
    print(f"yt-dlp is now version {ytdlp_version()}\n")
    return True


def find_js_runtime() -> tuple[str, str | None] | None:
    """Return (executable, --js-runtimes value) for the first runtime found."""
    for exe, flag in JS_RUNTIMES:
        if shutil.which(exe):
            return exe, flag
    return None


def ytdlp_debug_header() -> str:
    """Return yt-dlp's own --verbose debug header (works offline, no URL)."""
    try:
        out = subprocess.run(["yt-dlp", "-v", "--no-update"],
                             capture_output=True, text=True, timeout=60)
        return (out.stderr or "") + (out.stdout or "")
    except Exception:
        return ""


def ytdlp_interpreter() -> str:
    """Best guess at the Python interpreter that actually runs yt-dlp.

    This matters: yt-dlp on PATH is often installed under a *different*
    interpreter than the one running this script (conda env vs system vs
    Homebrew). Installing yt-dlp-ejs into the wrong one changes nothing.
    """
    exe = shutil.which("yt-dlp")
    if exe:
        try:
            with open(exe, "r", errors="ignore") as fh:
                first = fh.readline().strip()
            if first.startswith("#!") and "python" in first:
                # Strip a leading "#!" and any "/usr/bin/env " wrapper.
                path = first[2:].strip()
                if path.startswith("/usr/bin/env "):
                    path = path.split(None, 1)[1].strip()
                if path:
                    return path
        except OSError:
            pass
    return sys.executable


def has_ejs() -> bool:
    """True if yt-dlp itself reports the yt-dlp-ejs solver as loaded.

    Asking yt-dlp is authoritative; importing yt_dlp_ejs here would only test
    *this* script's interpreter, which is frequently not the one yt-dlp uses.
    """
    header = ytdlp_debug_header()
    if header:
        for line in header.splitlines():
            if "Optional libraries" in line:
                return "yt_dlp_ejs" in line
    # Fall back to importing locally if the header was unavailable.
    try:
        import yt_dlp_ejs  # noqa: F401
        return True
    except ImportError:
        return False


def preflight(remote_ejs: bool = False) -> tuple[bool, list[str]]:
    """Check that YouTube downloads can actually succeed.

    Returns (ok, extra_ytdlp_args). YouTube requires a JavaScript runtime plus
    the yt-dlp-ejs solver scripts; without them yt-dlp reports
    'Signature solving failed' and only image formats are offered, which
    surfaces as 'Requested format is not available'.
    """
    problems = []
    extra_args: list[str] = []

    runtime = find_js_runtime()
    if runtime is None:
        problems.append(
            "No JavaScript runtime found (need deno, node, or qjs).\n"
            "    Fix:  brew install deno")
    else:
        exe, flag = runtime
        if flag:
            extra_args += ["--js-runtimes", flag]
        print(f"JS runtime: {exe}")

    if remote_ejs:
        extra_args += ["--remote-components", "ejs:github"]
        print("EJS challenge solver: fetching from GitHub at runtime")
    elif not has_ejs():
        interp = ytdlp_interpreter()
        note = ""
        if interp != sys.executable:
            note = (f"\n    NOTE: yt-dlp runs under a different interpreter than "
                    f"this script\n          ({interp})\n          rather than "
                    f"({sys.executable}).\n          Install into yt-dlp's "
                    f"interpreter, shown above.")
        problems.append(
            "yt-dlp-ejs challenge solver not available to yt-dlp.\n"
            f"    Fix:  \"{interp}\" -m pip install -U 'yt-dlp[default]'{note}\n"
            "    Or, with no install at all, add to your yt-dlp config:\n"
            "          --remote-components ejs:github")
    else:
        print("EJS challenge solver: installed")

    if shutil.which("ffmpeg") is None:
        problems.append(
            "ffmpeg not found; yt-dlp needs it to convert audio to mp3.\n"
            "    Fix:  brew install ffmpeg")
    else:
        print("ffmpeg: found")

    if problems:
        print("\nPREFLIGHT FAILED - YouTube downloads will not work:\n",
              file=sys.stderr)
        for p in problems:
            print(f"  * {p}", file=sys.stderr)
        print(file=sys.stderr)
        return False, extra_args

    print()
    return True, extra_args


def warn_if_stale(version: str | None) -> None:
    """Print a loud warning if the installed yt-dlp looks old."""
    if not version:
        return
    try:
        released = dt.date(*(int(p) for p in version.split(".")[:3]))
    except (ValueError, TypeError):
        return
    age = (dt.date.today() - released).days
    if age > 90:
        print(f"WARNING: yt-dlp {version} is {age} days old. YouTube downloads "
              f"commonly fail with\n         'content is not available on this "
              f"app' on stale versions.\n         Re-run with --update-ytdlp to "
              f"upgrade it.\n")


# Player clients to try when a download 403s, in order.
#
# A 403 on the media URL means YouTube rejected the format link itself, which
# happens when the client is required to present a GVS PO Token and cannot.
# These clients are documented as NOT requiring a GVS PO Token, so they are
# the useful ones to fall back through. (`android_vr` is yt-dlp's current
# default and is presently 403-ing on many videos -- yt-dlp issue #17456.)
#
# This is a different failure from "Signature solving failed", which no amount
# of client-switching fixes; that one needs the JS runtime + EJS solver.
# Verified working on 2026-08-19: web_embedded downloaded 5/5 songs, while
# yt-dlp's default (android_vr) 403'd on 5/5. web_embedded is therefore tried
# FIRST -- leading with the default just burns an extra search per song.
# `None` (yt-dlp's own choice) stays in the list so this self-heals if
# web_embedded ever breaks and the default starts working again.
PLAYER_CLIENT_FALLBACKS = [
    "web_embedded",   # no PO Token required -- current known-good
    None,             # yt-dlp's default choice
    "tv",             # no PO Token required when cookies are supplied
    "web_safari",
    "mweb",
]


def download_song(name: str, artist: str, week_dir: Path, archive: Path,
                  extra_args: list[str], clients: list[str | None]
                  ) -> tuple[bool, str | None, str | None]:
    """Search YouTube for the song's music video and save audio as mp3.

    Retries across player clients, because a 403 on the media URL is usually
    specific to the client that produced the format URL.

    Returns (ok, youtube_id, player_client_used).
    """
    query = f"{artist} {name} official music video"
    out_template = str(week_dir / f"{sanitize(artist)} - {sanitize(name)}.%(ext)s")
    print(f"  -> yt-dlp search: {query!r}")

    for attempt, client in enumerate(clients):
        cmd = [
            "yt-dlp",
            f"ytsearch1:{query}",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--no-playlist",
            "--download-archive", str(archive),
            "--no-overwrites",
            "--no-update",
            "--output", out_template,
            *extra_args,
        ]
        if client:
            cmd += ["--extractor-args", f"youtube:player_client={client}"]
        if attempt:
            print(f"     403 or failure - retrying with player_client="
                  f"{client or 'default'}")
        before = _archive_ids(archive)
        if subprocess.run(cmd).returncode == 0:
            if attempt:
                print(f"     succeeded with player_client={client or 'default'}")
            # The download archive gains a "youtube <id>" line per success, so
            # diffing it recovers the video id without a second network call.
            new_ids = _archive_ids(archive) - before
            return True, (new_ids.pop() if new_ids else None), client
    return False, None, None


def _archive_ids(archive: Path) -> set[str]:
    """Video ids currently recorded in the yt-dlp download archive."""
    if not archive.exists():
        return set()
    ids = set()
    for line in archive.read_text(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            ids.add(parts[1])
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"Path to listening_history.db (default: {DEFAULT_DB})")
    parser.add_argument("--top", type=int, default=10,
                        help="How many songs to download (default: 10)")
    parser.add_argument("--days", type=int, default=7,
                        help="Look-back window in days (default: 7)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the top songs without downloading anything")
    parser.add_argument("--update-ytdlp", action="store_true",
                        help="Upgrade yt-dlp (with EJS solver) via pip first")
    parser.add_argument("--doctor", action="store_true",
                        help="Check the download environment and exit")
    parser.add_argument("--remote-ejs", action="store_true",
                        help="Let yt-dlp fetch the EJS solver from GitHub at "
                             "runtime instead of requiring the pip package")
    parser.add_argument("--client", action="append", metavar="NAME",
                        help="Force a specific YouTube player client (repeatable "
                             "to set your own fallback order). Default order: "
                             + ", ".join(c or "default"
                                         for c in PLAYER_CLIENT_FALLBACKS))
    parser.add_argument("--library-db", type=Path, default=None,
                        help=f"Library database tracking downloads/analysis "
                             f"(default: {library_db.DEFAULT_DB})")
    parser.add_argument("--redownload", action="store_true",
                        help="Ignore the library and re-download everything")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        help="Pass browser cookies to yt-dlp (e.g. safari, "
                             "chrome, firefox). Enables the 'tv' client and "
                             "bypasses most PO Token requirements. Note: using "
                             "your account carries a ban risk.")
    args = parser.parse_args()

    if args.doctor:
        if shutil.which("yt-dlp") is None:
            print("yt-dlp: NOT FOUND on PATH", file=sys.stderr)
            return 1
        version = ytdlp_version()
        print(f"yt-dlp: {version}")
        warn_if_stale(version)
        ok, _ = preflight(args.remote_ejs)
        print("Environment looks good." if ok else "Environment needs fixes.")
        return 0 if ok else 1

    if not args.db.exists():
        print(f"ERROR: database not found at {args.db}", file=sys.stderr)
        return 1

    songs = get_top_songs(args.db, args.top, args.days)
    if not songs:
        print(f"No plays found in the last {args.days} days.")
        return 0

    print(f"Top {len(songs)} songs from the last {args.days} days "
          f"(healing-music playlist excluded):\n")
    for i, (name, artist, plays, minutes) in enumerate(songs, 1):
        print(f"  {i:2d}. {artist} - {name}   ({plays} plays, {minutes} min)")
    print()

    if args.dry_run:
        print("Dry run: nothing downloaded.")
        return 0

    if shutil.which("yt-dlp") is None:
        print("ERROR: yt-dlp not found on PATH. Install it or add it to PATH.",
              file=sys.stderr)
        return 1

    if args.update_ytdlp and not update_ytdlp():
        return 1

    warn_if_stale(ytdlp_version())

    ok, extra_args = preflight(args.remote_ejs)
    if args.cookies_from_browser:
        extra_args += ["--cookies-from-browser", args.cookies_from_browser]
        print(f"Cookies: from {args.cookies_from_browser}")
    clients: list[str | None] = (list(args.client) if args.client
                                 else list(PLAYER_CLIENT_FALLBACKS))
    if not ok:
        print("Aborting before download. Fix the items above, or re-run with "
              "--update-ytdlp.", file=sys.stderr)
        return 1

    week_start = dt.date.today() - dt.timedelta(days=args.days)
    week_dir = DOWNLOADS_DIR / f"week_of_{week_start.isoformat()}"
    week_dir.mkdir(parents=True, exist_ok=True)
    archive = DOWNLOADS_DIR / "download_archive.txt"

    library = library_db.connect(args.library_db)
    week_of = week_start.isoformat()

    failures, skipped, fetched = [], [], []
    for i, (name, artist, plays, minutes) in enumerate(songs, 1):
        print(f"[{i}/{len(songs)}] {artist} - {name}")

        if not args.redownload:
            seen, reason = library_db.already_downloaded(library, name, artist,
                                                         DOWNLOADS_DIR)
            if seen:
                print(f"     skip - {reason}")
                skipped.append(f"{artist} - {name}")
                # Keep the ranking fresh even when the audio is reused.
                library_db.record_download(
                    library, name=name, artist=artist, week_of=week_of,
                    plays=plays, minutes=minutes)
                continue
            if reason:
                print(f"     {reason}")

        ok, video_id, client_used = download_song(
            name, artist, week_dir, archive, extra_args, clients)
        if ok:
            expected = week_dir / f"{library_db.safe_filename(name, artist)}.mp3"
            library_db.record_download(
                library, name=name, artist=artist, week_of=week_of,
                file_path=expected if expected.exists() else None,
                youtube_id=video_id, player_client=client_used or "default",
                plays=plays, minutes=minutes, status="downloaded")
            fetched.append(f"{artist} - {name}")
        else:
            library_db.record_download(
                library, name=name, artist=artist, week_of=week_of,
                plays=plays, minutes=minutes, status="failed")
            failures.append(f"{artist} - {name}")

    print(f"\nDone. Files are in: {week_dir}")
    print(f"  downloaded: {len(fetched)}   skipped (already had): "
          f"{len(skipped)}   failed: {len(failures)}")
    stats = library_db.summary(library)
    print(f"  library: {stats['downloads']} songs across {stats['weeks']} week(s), "
          f"{stats['analysed']} analysed")

    if failures:
        print("Failed to download:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
