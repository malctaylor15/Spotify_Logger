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
| **Does** | `run_notebook.sh` → Papermill → `get_latest_songs_prod.ipynb` |
| **Writes** | `Listening_History`, `Artists_Info` in `data/listening_history.db` |
| **Schedule** | Every 2 hours via cron |

Reads `max(after_ts)` from the database as a cursor, so each run only requests
plays newer than the last successful pull. On failure the script emails an
alert via `alerting.py` and keeps the executed notebook under `run_notebooks/`
alongside its log. New artist IDs are diffed against `Artists_Info` and only
missing ones are fetched.

### Reporting — weekly and monthly analysis

| | |
|---|---|
| **Entry points** | `bash run_email_notebook.sh` (14 days), `bash run_analysis_monthly.sh` (30 days) |
| **Does** | Runs `Weekly_Analysis.ipynb`, computes aggregates, renders plots, emails an HTML report |
| **Parameters** | `db_location`, `look_back_days`, `send_email` |
| **Dry run** | `-p send_email False` builds the report and plots without emailing |

### Weekly downloader — top songs as audio

| | |
|---|---|
| **Entry point** | `bash run_weekly_stems.sh`, or `.venv/bin/python weekly_downloader/download_weekly.py` |
| **Does** | Ranks the top N songs of the last 7 days, excluding the healing-music playlist, and fetches each from YouTube as mp3 |
| **Writes** | `weekly_downloader/downloads/week_of_<date>/` (gitignored) |
| **Health check** | `--doctor` verifies yt-dlp, JS runtime, EJS solver, ffmpeg |

### Stem analysis — which parts of a song are interesting

| | |
|---|---|
| **Entry point** | `.venv/bin/python weekly_downloader/analyze_stems.py --send` |
| **Does** | Separates each track locally with Demucs, scores the four stems, deletes the WAVs, emails recommendations |
| **Scoring** | Composite of loudness (0.35), spectral variation (0.25), activity (0.20), dynamics (0.10), entropy (0.10), relative within each song |

Separation runs entirely offline. `separate_stems.py` is an optional cloud
alternative using the Music AI API.

### Maintenance

| Task | Command |
|---|---|
| Configure Spotify credentials | `.venv/bin/python config_helper.py` |
| Verify saved credentials | `.venv/bin/python config_helper.py --check-only` |
| Back up the database to S3 | `bash aws_backup.sh` |

`weekly_downloader/upgrade_python.sh`, `cleanup_repo.sh` and
`commit_weekly_downloader.sh` were written for a Mac and hardcode
`/Users/malcolmtaylor/...` paths. They do not work on this machine.

---

## Environment

One environment for the whole repo: the repo-local `.venv`, which all four
`run_*.sh` scripts activate.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt        # ingestion + reporting
.venv/bin/python -m pip install -r requirements-audio.txt  # + weekly_downloader
```

`requirements-audio.txt` is separate only because Demucs pulls torch (~2GB),
which neither ingestion nor reporting needs. `ffmpeg` and `deno` must be on
PATH for the downloader and are installed outside pip.

---

## Data model

`data/listening_history.db` — note the `data/` prefix. Every entry point takes
this path as a papermill parameter or CLI flag; nothing hardcodes it.

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

Spotify app credentials live in a gitignored `.env` at the repo root; every
other secret lives in the gitignored `data/` directory.

| File | Holds |
|---|---|
| `.env` | `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI` (see `.env.sample`) |
| `data/.spotify_cache` | OAuth token cache — shared by ingestion *and* reporting |
| `data/email_pw.pkl` | Gmail app password for report emails |
| `data/musicai_api_key.txt` | Music AI key, only if using the cloud stem API |

For local development the redirect URI must be a loopback address such as
`http://127.0.0.1:8888/callback` — Spotify no longer accepts `localhost`. On a
headless server, point it at a reachable HTTPS hostname and use
`config_helper.py --remote`.

---

## Failure alerting

Every scheduled job tees its output to `run_logs/<job>_<date>.log`, takes the
real exit status from `${PIPESTATUS[0]}`, and on failure calls
`python -m alerting`, which emails the job name, exit status, host, time and
the tail of the log. The executed notebook is deleted on success and kept on
failure.

`alerting.py` never raises — an alerting problem must not mask the failure it
is reporting. To exercise a job's failure path without sending mail:

```bash
ALERT_DRY_RUN=1 bash run_email_notebook.sh
```

---

## Database migrations

Schema changes live in `migrations/`, numbered in apply order. Each is
idempotent, supports `--dry-run` (the default) and `--apply`, and takes a
timestamped backup before writing.

| Migration | Does |
|---|---|
| `000_remove_verification_rows.py` | One-off: drops `Metrics_WoW` rows left by verification runs on a date with no real report |
| `001_report_table_keys.py` | Adds `look_back_days` to `Metrics_WoW` (backfilled by inverting `Spotify Cost`), dedupes both report tables keeping the newest row per key, and adds the UNIQUE indexes |

After `001`, `Weekly_Analysis.ipynb` writes via `INSERT OR REPLACE`, so
re-running a report refreshes its rows instead of appending duplicates.

---

## Scheduled jobs

| Job | Cadence | Command |
|---|---|---|
| Ingestion | Every 2 hours | `bash run_notebook.sh` |
| Weekly report | Sundays | `bash run_email_notebook.sh` |
| Monthly report | Monthly | `bash run_analysis_monthly.sh` |
| Weekly download + stems | Sundays | `bash run_weekly_stems.sh` |

Every script activates the repo-local `.venv` and exits with the papermill
status, so cron sees failures. Each has its crontab line in a trailing comment.

The machine must be awake and online at the scheduled time.

---

## Known rough edges

- `EmailSender` is an external package outside this repo
  (`EMAILSENDER_PATH`, default `/home/malcolm/EmailSender1/`), used to send the
  weekly report. Failure *alerts* are centralised in `alerting.py`, but the two
  report emails (`Weekly_Analysis.ipynb` and
  `weekly_downloader/analyze_stems.py`) still each have their own sending code.
- Ingestion failures can still be silent when the cursor read succeeds but the
  write does not; there is no row-count or schema validation after inserts.
- Spotify's algorithmic playlists (`37i9dQZF1E4...`) return 404 to the
  playlist endpoint. `Weekly_Analysis.ipynb` logs and skips them, so they
  appear as blank rows in the playlist table.
- `run_notebooks/` accumulates executed notebooks from failed runs and is never
  pruned automatically.
- YouTube changes break yt-dlp periodically — see
  [WORKING_SETUP.md](weekly_downloader/WORKING_SETUP.md) for the three failure
  modes and their distinct fixes.
- [docs/pipeline_analysis.md](docs/pipeline_analysis.md) and
  [getting_started.txt](getting_started.txt) describe an older credential and
  runner layout. Treat this file and the README as authoritative where they
  disagree.
