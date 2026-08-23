# Spotify Logger — Documentation Index

Start here. This file is the table of contents for every document in the
repository, plus a summary of what the project actually does.

---

## What this repository does

Two systems share one SQLite database.

**1. Listening-history logger (the original project).** Spotify only exposes
the last ~50 plays, so a scheduled job polls the Web API every two hours,
appends new plays to SQLite, and enriches artists with genre and popularity
metadata. Over time this builds the complete listening history Spotify won't
give you. A weekly/monthly job reads that history and emails an analysis
report.

**2. Weekly downloader (`weekly_downloader/`).** Reads the same database,
finds the week's most-played songs, downloads each from YouTube as mp3,
optionally separates them into stems locally with Demucs, and emails a
recommendation of which stems are worth exploring.

```
Spotify Web API
      │  every 2 hours
      ▼
Listening_History ──┬──► Weekly_Analysis.ipynb ──► emailed analysis report
Artists_Info        │
(data/listening_history.db)
                    │
                    └──► weekly_downloader/
                            download_weekly.py  ──► top-N mp3s
                            analyze_stems.py    ──► emailed stem picks
```

---

## Documentation map

| Document | Covers | Read it when |
|---|---|---|
| [README.md](README.md) | Project overview, Spotify app setup, OAuth, how to run ingestion | Setting the project up for the first time |
| [docs/pipeline_analysis.md](docs/pipeline_analysis.md) | Deep analysis of the legacy pipeline: components, data flow, DB schema, failure points, observability gaps, next steps | Debugging ingestion, or changing how data is written |
| [weekly_downloader/README.md](weekly_downloader/README.md) | Downloader usage, stem scoring method, email config, yt-dlp troubleshooting | Using or modifying the weekly downloader |
| [weekly_downloader/WORKING_SETUP.md](weekly_downloader/WORKING_SETUP.md) | The verified-working yt-dlp configuration and the three distinct YouTube failure modes | Downloads suddenly break |
| [getting_started.txt](getting_started.txt) | Legacy runbook: credential pickles, cron, AWS backup | Historical reference — parts are outdated |

---

## Main functionality

### Ingestion — capture plays before they disappear

| | |
|---|---|
| **Entry point** | `bash run_notebook.sh` |
| **Does** | `run_notebook.sh` → `Run_Notebooks.ipynb` (Papermill wrapper) → `get_latest_songs_prod.ipynb` |
| **Writes** | `Listening_History`, `Artists_Info` in `data/listening_history.db` |
| **Schedule** | Every 2 hours via cron |

Reads `max(after_ts)` from the database as a cursor, so each run only requests
plays newer than the last successful pull. The wrapper notebook captures any
exception and emails an alert, keeping the failed executed notebook under
`run_notebooks/` for inspection. New artist IDs are diffed against
`Artists_Info` and only missing ones are fetched.

### Reporting — weekly and monthly analysis

| | |
|---|---|
| **Entry points** | `bash run_email_notebook.sh` (14 days), `bash run_analysis_monthly.sh` (30 days) |
| **Does** | Runs `Weekly_Analysis.ipynb`, computes aggregates, renders plots, emails an HTML report |

### Weekly downloader — top songs as audio

| | |
|---|---|
| **Entry point** | `python3 weekly_downloader/download_weekly.py` |
| **Does** | Ranks the top N songs of the last 7 days, excluding the healing-music playlist, and fetches each from YouTube as mp3 |
| **Writes** | `weekly_downloader/downloads/week_of_<date>/` (gitignored) |
| **Health check** | `--doctor` verifies yt-dlp, JS runtime, EJS solver, ffmpeg |

### Stem analysis — which parts of a song are interesting

| | |
|---|---|
| **Entry point** | `python3 weekly_downloader/analyze_stems.py --send` |
| **Does** | Separates each track locally with Demucs, scores the four stems, deletes the WAVs, emails recommendations |
| **Scoring** | Composite of loudness (0.35), spectral variation (0.25), activity (0.20), dynamics (0.10), entropy (0.10), relative within each song |

Separation runs entirely offline. `separate_stems.py` is an optional cloud
alternative using the Music AI API.

### Maintenance

| Task | Command |
|---|---|
| Configure Spotify credentials | `python3 config_helper.py` |
| Verify saved credentials | `python3 config_helper.py --check-only` |
| Back up the database to S3 | `bash aws_backup.sh` |
| Rebuild venvs on a newer Python | `bash weekly_downloader/upgrade_python.sh --check` |
| Clean the repo before committing | `bash weekly_downloader/cleanup_repo.sh` |

---

## Data model

`data/listening_history.db` — note the `data/` prefix. A sample
`listening_history.db` at the repo root is **not** the runtime database.

**`Listening_History`** — one row per play:

| Column | Notes |
|---|---|
| `name`, `artist_name` | Track title and first artist |
| `played_at_date`, `played_at_time` | Local-adjusted timestamp |
| `duration_min`, `popularity` | Track length and Spotify popularity |
| `song_uri`, `artist_id` | Spotify URIs |
| `playlist_id` | Playback context — playlist, album, or artist URI; `NULL` when played outside one |
| `after_ts` | Spotify cursor; `max()` drives the next incremental pull |

**`Artists_Info`** — one row per artist: `artist_name`, `uri`, `artist_id`,
`followers`, `genres`, `popularity`, `pull_date`.

`playlist_id` is what the downloader uses to exclude healing-music tracks.

---

## Configuration and secrets

Everything sensitive lives in the gitignored `data/` directory.

| File | Holds |
|---|---|
| `data/spotify_credentials.pkl` | Spotify client ID, secret, redirect URI |
| `data/.spotify_cache` | OAuth token cache |
| `data/email_pw.pkl` | Gmail app password for report emails |
| `data/musicai_api_key.txt` | Music AI key, only if using the cloud stem API |

Redirect URI must be exactly `http://127.0.0.1:8888/callback` — Spotify no
longer accepts `localhost`.

---

## Scheduled jobs

| Job | Cadence | Command |
|---|---|---|
| Ingestion | Every 2 hours | `bash run_notebook.sh` |
| Weekly report | Sundays | `bash run_email_notebook.sh` |
| Monthly report | Monthly | `bash run_analysis_monthly.sh` |
| Weekly download | Optional | `python3 weekly_downloader/download_weekly.py` |

The machine must be awake and online at the scheduled time.

---

## Known rough edges

Detailed in [docs/pipeline_analysis.md](docs/pipeline_analysis.md) §5 and §7:

- Some legacy notebooks reference `../credentials/` paths that no longer match
  `config_helper.py`'s `data/` layout.
- Ingestion failures can be silent when the cursor read succeeds but the write
  does not.
- No row-count or schema validation after inserts.
- YouTube changes break yt-dlp periodically — see
  [WORKING_SETUP.md](weekly_downloader/WORKING_SETUP.md) for the three failure
  modes and their distinct fixes.
