"""Shared library database for the weekly downloader.

Tracks what has already been downloaded and analysed so neither job repeats
work, and stores every metric that feeds the recommendation email so the email
can be rebuilt later without the audio.

Two tables:

  downloads       one row per song ever downloaded -- name, artist, the week it
                  was fetched for, when, where the file landed, and the play
                  counts it was ranked on.

  stem_analysis   one row per stem per song -- rank, composite score and every
                  raw and normalised measure behind it, plus the rendered
                  description. Enough to regenerate the email exactly.

The database lives in the repo's gitignored data/ directory alongside the
Spotify credentials and listening history.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_DB = REPO_ROOT / "data" / "weekly_downloader.db"
DOWNLOADS_DIR = HERE / "downloads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    artist_name     TEXT NOT NULL,
    match_key       TEXT NOT NULL UNIQUE,   -- normalised artist|title
    week_of         TEXT NOT NULL,
    downloaded_at   TEXT NOT NULL,
    file_path       TEXT,
    file_bytes      INTEGER,
    youtube_id      TEXT,
    player_client   TEXT,
    plays           INTEGER,
    minutes         REAL,
    status          TEXT NOT NULL DEFAULT 'downloaded'
);

CREATE INDEX IF NOT EXISTS idx_downloads_week ON downloads(week_of);

CREATE TABLE IF NOT EXISTS stem_analysis (
    id              INTEGER PRIMARY KEY,
    download_id     INTEGER NOT NULL REFERENCES downloads(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,          -- denormalised so the email can be
    artist_name     TEXT NOT NULL,          -- rebuilt from this table alone
    week_of         TEXT NOT NULL,
    analysed_at     TEXT NOT NULL,
    model           TEXT NOT NULL,
    stem            TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    score           REAL NOT NULL,
    rms_db          REAL,
    peak_db         REAL,
    crest_db        REAL,
    activity        REAL,
    flux            REAL,
    entropy         REAL,
    n_loudness      REAL,
    n_variation     REAL,
    n_activity      REAL,
    n_dynamics      REAL,
    n_entropy       REAL,
    description     TEXT,
    UNIQUE(download_id, stem)
);

CREATE INDEX IF NOT EXISTS idx_stems_week ON stem_analysis(week_of);
"""


# ------------------------------------------------------------- connection ---

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the library database."""
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------- match keys ---

def match_key(name: str, artist: str) -> str:
    """Normalised identity for a song, so trivial variations still match.

    Case, accents and punctuation differ between Spotify titles and filenames
    ("Djon'Maya" vs "Djôn'Maya"), which would otherwise cause the same track to
    be downloaded twice in different weeks.
    """
    def norm(text: str) -> str:
        text = unicodedata.normalize("NFKD", str(text))
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = "".join(c for c in text.lower() if c.isalnum() or c.isspace())
        return " ".join(text.split())   # collapse runs of whitespace
    return f"{norm(artist)}|{norm(name)}"


def safe_filename(name: str, artist: str) -> str:
    """The stem of the filename download_weekly.py writes."""
    bad = '/\\:*?"<>|'
    clean = lambda t: "".join("_" if ch in bad else ch for ch in str(t)).strip()
    return f"{clean(artist)} - {clean(name)}"


# ------------------------------------------------------------ disk lookup ---

def find_on_disk(name: str, artist: str,
                 downloads_dir: Path | None = None) -> Path | None:
    """Look for an already-downloaded mp3 of this song in ANY week folder.

    The database is authoritative, but a file can exist without a row (older
    downloads, a deleted database). Checking disk too means we never re-fetch
    audio that is already sitting there.
    """
    root = Path(downloads_dir) if downloads_dir else DOWNLOADS_DIR
    if not root.is_dir():
        return None
    wanted = match_key(name, artist)
    for candidate in root.glob("week_of_*/*.mp3"):
        stem = candidate.stem
        # Filenames are "Artist - Title"; split on the first " - ".
        if " - " in stem:
            f_artist, f_name = stem.split(" - ", 1)
            if match_key(f_name, f_artist) == wanted:
                return candidate
        if match_key(stem, artist) == wanted:
            return candidate
    return None


# -------------------------------------------------------------- downloads ---

def get_download(conn: sqlite3.Connection, name: str,
                 artist: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM downloads WHERE match_key = ?",
        (match_key(name, artist),)).fetchone()


def record_download(conn: sqlite3.Connection, *, name: str, artist: str,
                    week_of: str, file_path: Path | None = None,
                    youtube_id: str | None = None,
                    player_client: str | None = None,
                    plays: int | None = None, minutes: float | None = None,
                    status: str = "downloaded") -> int:
    """Insert or update the row for a song. Returns its download id."""
    size = None
    if file_path and Path(file_path).exists():
        size = Path(file_path).stat().st_size
    conn.execute("""
        INSERT INTO downloads (name, artist_name, match_key, week_of,
                               downloaded_at, file_path, file_bytes,
                               youtube_id, player_client, plays, minutes, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(match_key) DO UPDATE SET
            file_path     = COALESCE(excluded.file_path, downloads.file_path),
            file_bytes    = COALESCE(excluded.file_bytes, downloads.file_bytes),
            youtube_id    = COALESCE(excluded.youtube_id, downloads.youtube_id),
            player_client = COALESCE(excluded.player_client, downloads.player_client),
            plays         = COALESCE(excluded.plays, downloads.plays),
            minutes       = COALESCE(excluded.minutes, downloads.minutes),
            status        = excluded.status
    """, (name, artist, match_key(name, artist), week_of, now(),
          str(file_path) if file_path else None, size, youtube_id,
          player_client, plays, minutes, status))
    conn.commit()
    row = get_download(conn, name, artist)
    return int(row["id"])


def already_downloaded(conn: sqlite3.Connection, name: str, artist: str,
                       downloads_dir: Path | None = None
                       ) -> tuple[bool, str | None]:
    """(True, reason) if this song should be skipped."""
    row = get_download(conn, name, artist)
    if row and row["status"] == "downloaded":
        path = row["file_path"]
        if path and Path(path).exists():
            return True, f"already downloaded {row['downloaded_at'][:10]} -> {Path(path).name}"
        on_disk = find_on_disk(name, artist, downloads_dir)
        if on_disk:
            return True, f"already on disk -> {on_disk.parent.name}/{on_disk.name}"
        return False, "in database but file is missing; re-downloading"
    on_disk = find_on_disk(name, artist, downloads_dir)
    if on_disk:
        return True, f"found on disk -> {on_disk.parent.name}/{on_disk.name}"
    return False, None


# ---------------------------------------------------------- stem analysis ---

def is_analysed(conn: sqlite3.Connection, download_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM stem_analysis WHERE download_id = ?",
        (download_id,)).fetchone()
    return bool(row["n"])


def record_analysis(conn: sqlite3.Connection, *, download_id: int, name: str,
                    artist: str, week_of: str, model: str,
                    ranked_stems: list[tuple[str, dict]],
                    describe) -> None:
    """Persist every stem metric that feeds the email."""
    conn.execute("DELETE FROM stem_analysis WHERE download_id = ?", (download_id,))
    stamp = now()
    for rank, (stem, m) in enumerate(ranked_stems, 1):
        conn.execute("""
            INSERT INTO stem_analysis (download_id, name, artist_name, week_of,
                analysed_at, model, stem, rank, score, rms_db, peak_db, crest_db,
                activity, flux, entropy, n_loudness, n_variation, n_activity,
                n_dynamics, n_entropy, description)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (download_id, name, artist, week_of, stamp, model, stem, rank,
              m.get("score"), m.get("rms_db"), m.get("peak_db"), m.get("crest_db"),
              m.get("activity"), m.get("flux"), m.get("entropy"),
              m.get("n_loudness"), m.get("n_variation"), m.get("n_activity"),
              m.get("n_dynamics"), m.get("n_entropy"), describe(stem, m)))
    conn.commit()


def load_week_results(conn: sqlite3.Connection, week_of: str) -> list[dict]:
    """Rebuild the email payload for a week purely from stored metrics.

    Returns the same structure analyse_track() produces, so the report builders
    work unchanged and no audio is needed.
    """
    rows = conn.execute("""
        SELECT * FROM stem_analysis WHERE week_of = ?
        ORDER BY artist_name, name, rank
    """, (week_of,)).fetchall()

    by_track: dict[str, dict] = {}
    for r in rows:
        title = f"{r['artist_name']} - {r['name']}"
        entry = by_track.setdefault(title, {"track": title, "stems": {},
                                            "ranking": []})
        entry["stems"][r["stem"]] = {
            "score": r["score"], "rms_db": r["rms_db"], "peak_db": r["peak_db"],
            "crest_db": r["crest_db"], "activity": r["activity"],
            "flux": r["flux"], "entropy": r["entropy"],
            "n_loudness": r["n_loudness"], "n_variation": r["n_variation"],
            "n_activity": r["n_activity"], "n_dynamics": r["n_dynamics"],
            "n_entropy": r["n_entropy"], "silent": (r["score"] or 0) == 0,
            "description": r["description"],
        }
        entry["ranking"].append(r["stem"])
    return list(by_track.values())


def available_weeks(conn: sqlite3.Connection) -> list[str]:
    return [r["week_of"] for r in conn.execute(
        "SELECT DISTINCT week_of FROM stem_analysis ORDER BY week_of DESC")]


def summary(conn: sqlite3.Connection) -> dict:
    d = conn.execute("SELECT COUNT(*) n FROM downloads").fetchone()["n"]
    a = conn.execute(
        "SELECT COUNT(DISTINCT download_id) n FROM stem_analysis").fetchone()["n"]
    w = conn.execute("SELECT COUNT(DISTINCT week_of) n FROM downloads").fetchone()["n"]
    return {"downloads": d, "analysed": a, "weeks": w}
