# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

`DOCUMENTATION.md` is the documentation index — what the project does, every
entry point, the data model, and links to the other guides
(`weekly_downloader/README.md`, `weekly_downloader/WORKING_SETUP.md`). Prefer
it over re-deriving the pipeline from the notebooks.

## Commands

```bash
# Spotify credentials + OAuth (writes .env, caches token to data/.spotify_cache)
.venv/bin/python config_helper.py                 # local, opens a browser
.venv/bin/python config_helper.py --remote        # headless: paste-back URL flow
.venv/bin/python config_helper.py --check-only    # verify without re-entering

# Ingestion (cron: every 2 hours)
bash run_notebook.sh

# Exercise any job's failure path without sending mail
ALERT_DRY_RUN=1 bash run_email_notebook.sh

# Reporting (cron: weekly / monthly)
bash run_email_notebook.sh          # look_back_days=14
bash run_analysis_monthly.sh        # look_back_days=30

# Weekly downloader + stem analysis (cron: Sundays)
bash run_weekly_stems.sh
.venv/bin/python weekly_downloader/download_weekly.py --doctor    # check yt-dlp/deno/ffmpeg
.venv/bin/python weekly_downloader/download_weekly.py --dry-run
.venv/bin/python weekly_downloader/analyze_stems.py --report-only # score + HTML, no email
.venv/bin/python weekly_downloader/analyze_stems.py --from-db     # rebuild an email with no audio

# Backup
bash aws_backup.sh
```

**Build the report without sending mail** — use this to test changes to
`Weekly_Analysis.ipynb`; everything runs but the email step is skipped:

```bash
.venv/bin/papermill Weekly_Analysis.ipynb /tmp/out.ipynb \
  -p db_location data/listening_history.db -p send_email False --no-progress-bar
```

There is no test suite and no linter. `Slope/test_slope.py` belongs to the
vendored slope-graph library, not to this project. Verification means running
a notebook through papermill and checking the executed copy for error outputs.

## Environment

One environment for everything: the repo-local `.venv`. All four `run_*.sh`
scripts activate it.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt        # ingestion + reporting
.venv/bin/python -m pip install -r requirements-audio.txt  # + weekly_downloader
```

`requirements-audio.txt` is split out only because it pulls torch (~2GB) for
Demucs; nothing in ingestion or reporting needs it. `ffmpeg` and `deno` must
be on PATH for the downloader and are installed outside pip.

Pinning notes: `kaleido==0.2.1` renders plotly figures to PNG headlessly —
kaleido 1.x drives a real Chromium, which this box does not have. `pandas` is
2.x, so `DataFrame.append` and `applymap` are unavailable.

## Architecture

Notebooks are the production code. Papermill executes them; a cell tagged
`parameters` is the injection point. Changing runtime behaviour means editing
notebook cells, not a module. Each wrapper writes the executed copy to
`run_notebooks/` and deletes it on success, so anything left in that directory
is a failed run worth reading.

| Notebook | Parameters |
|---|---|
| `get_latest_songs_prod.ipynb` | `db_location` |
| `Weekly_Analysis.ipynb` | `db_location`, `look_back_days`, `send_email` |

**Every job has the same shape.** All four `run_*.sh` scripts activate `.venv`,
tee output to `run_logs/<job>_<date>.log`, take the real status from
`${PIPESTATUS[0]}` (not `$?`, which is tee's), and on failure call
`python -m alerting`. On success the executed notebook is deleted; on failure
it is kept under `run_notebooks/` next to its log.

**Failure alerting is centralised in `alerting.py`.** One module, used by every
job — it emails the job name, exit status, host, time and the log tail. It
never raises: an alerting problem must not mask the failure being reported.
Set `ALERT_DRY_RUN=1` to exercise a job's failure path without sending mail.
There used to be a `Run_Notebooks.ipynb` wrapper notebook that did this for
ingestion alone, with its own SMTP block; it was removed once every job alerted
uniformly, since it would have produced a second alert for the same failure.

**Incremental pull.** Ingestion reads `max(after_ts)` from `Listening_History`
and passes it as the Spotify `after` cursor, so a run only fetches plays newer
than the last successful one. The cursor lives in the data, not in a state
file — a bad write to `Listening_History` silently rewinds or stalls the
pipeline. New artist IDs are diffed against `Artists_Info` (rows fresher than
six months) so only unknown artists are fetched.

**Report tables are keyed, and writes upsert.** `Metrics_WoW` is
`UNIQUE(Date, look_back_days)` — the weekly (14d) and monthly (30d) reports for
the same day are separate rows, and re-running either refreshes its own row.
`Songs_by_date_WoW` is `UNIQUE(played_at_date)`: one row per calendar day,
replaced on each run so a later, more complete pull wins. `Weekly_Analysis.ipynb`
writes through an `upsert()` helper (`INSERT OR REPLACE`) because pandas
`.to_sql()` has no upsert mode — **don't revert those two cells to `to_sql(...,
if_exists='append')`**, it will fail on the constraint. Schema changes live in
`migrations/`, are idempotent, and take a backup before writing:

```bash
.venv/bin/python migrations/001_report_table_keys.py --dry-run
.venv/bin/python migrations/001_report_table_keys.py --apply
```

**One database, two consumers.** `data/listening_history.db` holds
`Listening_History` (one row per play) and `Artists_Info`, plus `Metrics_WoW`
and `Songs_by_date_WoW` written by the analysis notebook. `weekly_downloader/`
only *reads* it and keeps its own state in `data/weekly_downloader.db`
(`downloads`, `stem_analysis` via `library_db.py`). Downloader dedupe is a
normalised `artist|title` `match_key` plus a scan of the `week_of_*` folders on
disk, so a song is never fetched twice; stem metrics are denormalised
deliberately so `--from-db` can regenerate a past email with no mp3s present.

**`--top` is a target, not a slice.** `download_weekly.py` walks the weekly
chart in rank order until `--top` mp3s are actually sitting in *this* week's
folder (bounded by `--max-candidates`, default 4x), so a song YouTube refuses
is replaced from further down rather than lost; audio left over from an earlier
week does not fill a slot. `analyze_stems.py --min-tracks N` (default 2) then
guarantees the email carries N breakdowns, topping any shortfall up from the
newest stored analyses and labelling them as carried over. An empty week folder
still sends a report rather than exiting quietly.

`playlist_id` on `Listening_History` is the playback context URI (playlist,
album or artist, `NULL` outside one) and is what `EXCLUDED_PLAYLIST_IDS` in
`download_weekly.py` filters on.

**One token cache.** `config_helper.py`, `get_latest_songs_prod.ipynb` and
`Weekly_Analysis.ipynb` all authenticate through
`SpotifyOAuth(cache_path='data/.spotify_cache')`. Never pass `username=` to
`SpotifyOAuth` — spotipy then derives its own `.cache-<username>` file in the
working directory and you end up with two divergent caches.

## Vendored and external dependencies

`Slope/` is a vendored Tufte slope-graph library (MIT) providing
`plotSlope.slope` for the genre bump plot. It is committed, including two local
patches that upstream never had — see `Slope/VENDORED.md` before touching it.

One dependency still lives outside this repo:

- **`EmailSender`** — external package, default `/home/malcolm/EmailSender1/`,
  overridable with the `EMAILSENDER_PATH` environment variable. Both the import
  and the send are guarded, so the report still builds if it is missing.

`Weekly_Analysis.ipynb` attaches only images that actually rendered, so a
skipped plot cannot break the email.

## Secrets and gitignored state

Everything sensitive stays out of git: `.env` at the repo root (Spotify client
ID/secret/redirect URI, loaded via `dotenv.load_dotenv()`; `.env.sample`
documents the three keys), and the whole `data/` directory (`.spotify_cache`,
`email_pw.pkl`, `musicai_api_key.txt`, the databases). `images/`,
`run_notebooks/`, `*.log` and `weekly_downloader/downloads/` are gitignored too
— generated output should never be committed.

## Downloads break periodically

YouTube breakage is expected, not exceptional, and there are three distinct
failure modes with three different fixes (stale yt-dlp / missing JS runtime +
EJS solver / player client 403). `weekly_downloader/WORKING_SETUP.md` records
the last known-good configuration and how to tell them apart — read it before
changing download flags, and update it when a new player client wins.
