# Spotify Logger Pipeline Analysis

## 1. System Overview

### What the system does

This repository is a legacy Spotify listening-history ingestion and reporting pipeline. Despite any external references to video scraping, website scraping, image extraction, metadata scraping, or view-count history, the checked-in code does **not** implement a video or website scraper. The implemented system pulls a user's recently played Spotify tracks through the Spotify Web API, stores listening events in SQLite, enriches artists through Spotify artist APIs, and generates weekly/monthly listening-analysis emails and image reports.

### Main components

| Component | Files | Responsibility |
| --- | --- | --- |
| Spotify ingestion notebook | `get_latest_songs_prod.ipynb` | Authenticates with Spotify, reads the last stored cursor, fetches recently played tracks, parses track/context metadata, appends rows to SQLite, and appends new artist metadata. |
| Notebook runner / alert wrapper | `Run_Notebooks.ipynb`, `run_notebook.sh` | Executes ingestion with Papermill, saves executed notebooks under `run_notebooks/`, emails failures, and deletes successful run artifacts. |
| Reporting / analysis | `Weekly_Analysis.ipynb`, `run_email_notebook.sh`, `run_analysis_monthly.sh` | Reads listening history and artist info, computes weekly/monthly summaries, writes some aggregate tables, creates images, and emails an HTML report. |
| Backfill / exploratory maintenance | `Spotify Artist Genre - Backfill.ipynb`, `Playlist Chk Start.ipynb`, `Adhoc.ipynb` | Backfills missing artist genre metadata, experiments with playlist/source parsing, and runs ad-hoc database checks. |
| Database | `listening_history.db` sample; expected runtime DB at `data/listening_history.db` | SQLite store for listening events and expected enrichment/aggregate tables. |
| Setup / operations | `README.md`, `getting_started.txt`, `config_helper.py`, `aws_backup.sh`, `spotify_queries.sql` | Human setup notes, credential pickle helper, S3 backup script, and sample analysis queries. |

### End-to-end data flow

1. A scheduler such as cron runs `run_notebook.sh` every two hours, based on the setup notes.
2. `run_notebook.sh` activates a hard-coded virtualenv and runs `Run_Notebooks.ipynb` through Papermill, passing `db_location=data/listening_history.db`.
3. `Run_Notebooks.ipynb` runs `get_latest_songs_prod.ipynb` and captures any exception as a string.
4. `get_latest_songs_prod.ipynb` loads Spotify credentials from a pickle, initializes a Spotipy OAuth client, reads `max(after_ts)` from `Listening_History`, and calls `spotify.current_user_recently_played(after=latest_time_pull)`.
5. The notebook converts Spotify API JSON into one row per played track, including song name, first artist, local-adjusted date/time, duration, popularity, song URI, first artist URI, playback context URI, and Spotify cursor timestamp.
6. The parsed rows are appended to `Listening_History` with `pandas.DataFrame.to_sql(..., if_exists='append')`.
7. New artist IDs from the current pull are compared with `artists_info`; missing artists are fetched through `spotify.artists(...)`, parsed, and appended to `Artists_Info`.
8. Separate reporting scripts run `Weekly_Analysis.ipynb` weekly/monthly, which reads the DB, computes aggregates, attempts to save images under `images/`, writes weekly aggregate tables, and sends an email through an external `EmailSender` module.
9. `aws_backup.sh` optionally copies `data/listening_history.db` to S3 with a date-stamped object name.

## 2. Repository Structure

### Root files and notebooks

| Path | Role | Notes |
| --- | --- | --- |
| `README.md` | Project overview | Correctly describes Spotify history logging rather than video scraping. Lists the ingestion notebook, runner notebook, shell runner, and credential helper. |
| `getting_started.txt` | Manual setup runbook | Describes credential pickle setup, one-time Spotify OAuth bootstrap, cron setup, and AWS backup setup. Contains outdated/hard-coded paths. |
| `config_helper.py` | Credential pickle helper | Creates `data/spotify_credentials.pkl` and `data/email_pw.pkl`, but contains placeholder syntax (`<<...>>`) and does not match notebook credential paths. |
| `get_latest_songs_prod.ipynb` | Primary ingestion entry point | Pulls recent Spotify plays and artist metadata into SQLite. Intended to be run by `Run_Notebooks.ipynb`, not manually in production. |
| `Run_Notebooks.ipynb` | Ingestion wrapper entry point | Papermill wrapper around `get_latest_songs_prod.ipynb`; sends email on failure and deletes successful executed notebook. |
| `run_notebook.sh` | Production-ish shell entry point for ingestion | Changes to repo dir, activates `/home/malcolm/main/bin/activate`, executes `Run_Notebooks.ipynb`, and removes successful output notebook. |
| `Weekly_Analysis.ipynb` | Weekly/monthly reporting entry point | Reads listening history and artists, computes metrics, writes aggregate tables, creates plots, and sends an email. |
| `run_email_notebook.sh` | Shell entry point for weekly reporting | Runs `Weekly_Analysis.ipynb` with default `look_back_days=14`. |
| `run_analysis_monthly.sh` | Shell entry point for monthly reporting | Runs `Weekly_Analysis.ipynb` with `look_back_days=30`. |
| `Spotify Artist Genre - Backfill.ipynb` | Artist metadata backfill / playlist experiment | Fetches artist metadata for artists already in `Listening_History`; has incomplete/broken playlist parsing code. |
| `Playlist Chk Start.ipynb` | Playlist/source analysis experiment | Explores context URIs (`playlist`, `album`, `artist`) and playlist statistics; references a `Playlist_IDs` table. |
| `Adhoc.ipynb` | Manual DB inspection | Reads `Artists_Info` and checks genre/artist values. |
| `spotify_queries.sql` | Sample SQL snippets | Contains ad-hoc queries against `Listening_History` for date/artist summaries. |
| `aws_backup.sh` | Backup operation | Copies `data/listening_history.db` to S3 bucket `do-mt-backups/Listening_History/`. |
| `listening_history.db` | Sample SQLite DB | Checked-in example DB. It currently contains only `Listening_History`. Runtime scripts expect `data/listening_history.db`. |
| `demo.png`, `Plotly Orca Demo.ipynb` | Plotly export experiment | Not part of ingestion. |

### Entry points

- Ingestion: `bash run_notebook.sh` or direct development run of `papermill Run_Notebooks.ipynb run_notebooks/<output>.ipynb -p db_location data/listening_history.db`.
- Weekly report: `bash run_email_notebook.sh`.
- Monthly report: `bash run_analysis_monthly.sh`.
- One-off backfill: manually execute `Spotify Artist Genre - Backfill.ipynb` after validating credentials and DB path.
- Backup: `bash aws_backup.sh`.

## 3. Data Pipeline Flow

### Source system

The source is the Spotify Web API through Spotipy, specifically:

- `spotify.current_user_recently_played(after=...)` for recently played track events.
- `spotify.artists([...])` for batch artist enrichment.
- `spotify.artist(...)`, `spotify.album(...)`, `spotify.user_playlist(...)`, and `spotify.playlist(...)` in reporting/backfill experiments for source/context metadata.

There is no website scraper, HTML parser, image scraper, video metadata extractor, or view-count time-series collector in this repository.

### Ingestion flow: scrape/API pull -> parse -> transform -> store -> enrich

1. **Credential load**
   - `get_latest_songs_prod.ipynb` loads `../credentials/spotify_creds.pkl` and injects the dict into `os.environ`.
   - The expected keys are `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, and `SPOTIPY_REDIRECT_URI`.

2. **Spotify OAuth setup**
   - The notebook creates a Spotipy client with `SpotifyOAuth(scope="user-read-recently-played", username='malchemist02')`.
   - This requires a token cache/bootstrap flow outside the notebook automation.

3. **Cursor read**
   - The notebook connects to `db_location`, defaulting inside the notebook to `data/listening_history.db`.
   - It executes `select max(after_ts) from Listening_History`.
   - If the table/query fails, it catches the exception, prints it, and sets `latest_time_pull=None`, causing the API to return Spotify's latest available recent history rather than a precise continuation point.

4. **API pull**
   - `get_recently_played(after=latest_time_pull)` calls `spotify.current_user_recently_played(after=after)`.
   - No pagination loop is implemented. Each run processes only the single response returned by Spotipy.

5. **Parse**
   - `split_utc_time_str(time_str)` parses Spotify UTC timestamps and subtracts four hours to create `played_at_date` and `played_at_time`.
   - For each play item, `get_recently_played` extracts: track name, duration, popularity, track URI, first artist name, first artist URI, playback context URI, and the response-level `after` cursor.

6. **Transform**
   - Duration is converted from milliseconds to minutes.
   - Track URI is renamed from `uri` to `song_uri`.
   - Only the first artist is retained, even if Spotify returns multiple artists.
   - Playback context is stored generically as `playlist_id`, even though the context can be a playlist, album, artist, or another Spotify URI type.

7. **Store listening events**
   - Parsed rows are appended to `Listening_History` via `newly_played.to_sql('Listening_History', con, if_exists='append')`.
   - Because `index=False` is not passed, pandas writes the DataFrame index into an `index` column.
   - There is no unique key or duplicate prevention.

8. **Artist enrichment**
   - The notebook identifies unique artists in the current pull, compares them against recent rows from `artists_info`, fetches missing artists through `spotify.artists(new_artists_ids)`, parses each artist, and appends to `Artists_Info`.
   - This enrichment only runs for artists in the current ingestion batch; full repair/backfill is left to `Spotify Artist Genre - Backfill.ipynb`.

9. **Commit/close**
   - The notebook commits and closes the SQLite connection after event and artist writes.

### Reporting flow: read -> aggregate -> persist summaries -> render -> email

1. `Weekly_Analysis.ipynb` computes a report window from `look_back_days` and yesterday's date.
2. It reads `Listening_History` for that date range.
3. It computes total songs, unique songs, minutes/hours played, cost/hour, most listened artist/song, day-level summaries, time-of-day grouping, song-level summaries, genre summaries through a join to `artists_info`, and playlist/source summaries.
4. It appends `metrics2` to `Metrics_WoW` and `songs_by_date` to `Songs_by_date_WoW`.
5. It attempts to generate `images/bump_plot_<date>.png` and `images/listening_graphs_<date>.png`.
6. It sends an HTML email using an external module at `/home/malcolm/EmailSender1/EmailSender.py`.

### Scheduling and retries

- Scheduling is intended to be cron-based, not embedded in Python.
- `getting_started.txt` suggests running ingestion every two hours.
- `run_email_notebook.sh` and `run_analysis_monthly.sh` include weekly cron examples for report delivery.
- There is no retry loop for Spotify API calls, SQLite writes, email sends, image generation, or backups.
- The ingestion wrapper catches only notebook-level failures and sends email; successful-but-empty pulls, partial duplicates, failed artist enrichment after event insert, and missing expected rows are not explicitly validated.

## 4. Database Schema & Usage

### Important DB path distinction

- The repository contains `listening_history.db` at the root as an example database.
- Runtime scripts and notebooks generally expect `data/listening_history.db`.
- `Weekly_Analysis.ipynb` also contains hard-coded references to `/home/malcolm/Spotify_Logger/data/listening_history.db` and later `data/listening_history.db`.

This mismatch is a major operational risk: an engineer can run the pipeline successfully against one SQLite file while inspecting or backing up another.

### Tables observed or expected

The checked-in sample database currently has only one table: `Listening_History`. The notebooks create or reference additional tables during normal runtime. The table list below includes all tables discovered in the database file or source code.

#### `Listening_History`

**Purpose:** Fact table containing one row per Spotify recently played event captured by ingestion.

**Observed schema in checked-in `listening_history.db`:**

| Column | Type | Meaning |
| --- | --- | --- |
| `index` | INTEGER | Pandas DataFrame index written by `to_sql`; not a business key. |
| `name` | TEXT | Track name. |
| `artist_name` | TEXT | First artist name from Spotify track payload. |
| `played_at_date` | TEXT | Date derived from Spotify `played_at` after subtracting four hours. |
| `played_at_time` | TEXT | Time derived from Spotify `played_at` after subtracting four hours. |
| `duration_min` | REAL | Track duration in minutes. |
| `popularity` | INTEGER | Spotify track popularity at pull time. |
| `song_uri` | TEXT | Spotify track URI. |
| `artist_id` | TEXT | First artist Spotify URI, e.g. `spotify:artist:<id>`. |
| `playlist_id` | TEXT | Playback context URI; name is misleading because it may not be a playlist. |
| `after_ts` | TEXT | Spotify API cursor timestamp from the response. Used as the next run's lower bound. |

**Relationships:**

- `Listening_History.artist_id` is joined to `artists_info.artist_id` / `Artists_Info.artist_id` for genre reporting.
- `Listening_History.playlist_id` is explored as a source/context URI and sometimes left-joined to `Playlist_IDs.uri` in `Playlist Chk Start.ipynb`.

**Writes:**

- Inserted/appended in `get_latest_songs_prod.ipynb` with `newly_played.to_sql('Listening_History', con, if_exists='append')`.
- No update statements were found.

**Risks:**

- No primary key, uniqueness constraint, or de-duplication on `song_uri + played_at_date + played_at_time` or Spotify's exact `played_at` timestamp.
- `after_ts` is response-level cursor metadata, not per-track playback time, so every row in a batch can share the same cursor.
- The raw UTC `played_at` timestamp is not stored; debugging timezone/cursor issues is harder.
- The index column is an artifact of pandas writes.

#### `Artists_Info` / `artists_info`

**Purpose:** Artist dimension/enrichment table containing Spotify artist profile metadata.

**Expected columns from notebook writes:**

| Column | Meaning |
| --- | --- |
| `artist_name` | Spotify artist name. |
| `uri` | Bare Spotify artist ID without `spotify:artist:` prefix. |
| `artist_id` | Full artist URI with `spotify:artist:` prefix. |
| `followers` | Follower count at pull time. |
| `genres` | Stringified Python list of Spotify genres. |
| `popularity` | Spotify artist popularity at pull time. |
| `pull_date` | Date artist metadata was pulled. |

**Relationships:**

- Joined from `Listening_History.artist_id` to `artists_info.artist_id` in `Weekly_Analysis.ipynb`.

**Writes:**

- Appended in `get_latest_songs_prod.ipynb` when newly encountered artists are fetched.
- Appended in `Spotify Artist Genre - Backfill.ipynb` for backfill.

**Updates:**

- No update/upsert statements were found. Re-pulls append new rows rather than updating existing rows.

**Risks and inconsistencies:**

- Code uses both `Artists_Info` and `artists_info`. SQLite is case-insensitive for table names in typical usage, but mixed naming is confusing and can break portability.
- New artist detection appears inconsistent: `existing_artists['artist_id']` is prefixed with `spotify:artist:` even though the source column is already expected to be the full URI; the merge compares `unique_newly_played.artist_id` to `existing_artists.uri`, which is a bare ID. This can cause unnecessary re-fetching/appending or missed matches.
- The six-month filter SQL is assembled as `where pull_date > {six_months_ago_str}` without quotes around the date string, which is likely invalid or semantically wrong SQL.
- `genres` is stored as a string representation of a Python list, requiring `ast.literal_eval` in reports.

#### `Metrics_WoW`

**Purpose:** Weekly/monthly aggregate metrics table written by reporting.

**Expected columns:** Dynamic columns derived from `metrics2`, including metrics such as song counts, unique song counts, minutes/hours played, Spotify cost, cost/hour, most listened artist/song, and report dates. Exact schema depends on the DataFrame at write time.

**Writes:**

- Appended in `Weekly_Analysis.ipynb` with `metrics2.to_sql('Metrics_WoW', con, index=False, if_exists='append')`.

**Updates:**

- No updates/upserts were found.

**Risks:**

- Re-running the same report window appends duplicate aggregate rows.
- Dynamic DataFrame-driven schema can drift if notebook columns change.

#### `Songs_by_date_WoW`

**Purpose:** Per-day summary table written by reporting.

**Expected columns:** Date plus outputs from `get_date_metrics`, including number of songs, unique songs, minutes/hours played, cost metrics, most listened artist, and most listened song.

**Writes:**

- Appended in `Weekly_Analysis.ipynb` with `songs_by_date.reset_index().to_sql('Songs_by_date_WoW', con, index=False, if_exists='append')`.

**Updates:**

- No updates/upserts were found.

**Risks:**

- Re-running the same report window appends duplicates.
- Missing days are reindexed/fill-valued for plotting, so report-table rows can contain synthetic zero rows.

#### `Playlist_IDs`

**Purpose:** Appears intended as a playlist/context dimension with playlist names and track counts.

**Evidence and usage:**

- `Playlist Chk Start.ipynb` lists it among tables in notebook output and left joins `Listening_History.playlist_id` to `Playlist_IDs.uri`.
- No reliable creation/write path for `Playlist_IDs` was found in source code.

**Risks:**

- Reporting/playlist analysis relying on this table is incomplete.
- The table is absent from the checked-in sample DB.

### Insert/update summary

| Table | Inserts/appends | Updates/upserts | Current state |
| --- | --- | --- | --- |
| `Listening_History` | `get_latest_songs_prod.ipynb` | None | Present in sample DB. |
| `Artists_Info` / `artists_info` | `get_latest_songs_prod.ipynb`, `Spotify Artist Genre - Backfill.ipynb` | None | Expected runtime table; absent from sample DB. |
| `Metrics_WoW` | `Weekly_Analysis.ipynb` | None | Expected report-output table; absent from sample DB. |
| `Songs_by_date_WoW` | `Weekly_Analysis.ipynb` | None | Expected report-output table; absent from sample DB. |
| `Playlist_IDs` | No confirmed insert path | None | Referenced/experimental; absent from sample DB. |

## 5. Failure Points & Risks

### Scraping/API and authentication risks

- **Not a scraper:** The pipeline uses Spotify API calls, so website selector breakage is not a concern; OAuth/token expiry, API rate limits, and API payload shape changes are the relevant risks.
- **Credential path mismatch:** `config_helper.py` writes `data/spotify_credentials.pkl`, while notebooks load `../credentials/spotify_creds.pkl`. Email credentials have a similar mismatch.
- **Hard-coded user and paths:** Several notebooks use `username='malchemist02'`, `/home/malcolm/...`, and hard-coded Gmail addresses.
- **Manual OAuth dependency:** Initial token setup requires a manual browser/VNC workflow according to `getting_started.txt`.
- **No Spotify API retry/backoff:** API calls are not wrapped in rate-limit/backoff handling.
- **No pagination:** Recently played ingestion performs a single API call per run. If more plays exist than the API returns in one response between runs, older events may be skipped.

### Parsing and transformation risks

- **Timezone hard-code:** `split_utc_time_str` subtracts exactly four hours from UTC timestamps. This ignores daylight saving time, user timezone changes, and historical offset changes.
- **Loss of raw timestamp:** The raw Spotify `played_at` timestamp is discarded.
- **Multi-artist loss:** Only the first artist is captured for each track.
- **Misleading `playlist_id`:** The value is actually Spotify `context.uri`; it can be a playlist, album, artist, or absent.
- **Empty response handling:** When `recently_played['cursors'] is None` or `n_items == 0`, the function returns an empty DataFrame, and the notebook still calls `to_sql`. This may silently produce no data or create an empty table depending on DB state.
- **Incomplete playlist code:** `Spotify Artist Genre - Backfill.ipynb` contains a syntax error in `parse_all_tracks` (`sort_values('added_at')|`), so that section cannot run as-is.

### Database write risks

- **No idempotency:** Re-runs can duplicate listening events and aggregate report rows.
- **No schema migrations:** Tables are created implicitly by pandas `to_sql`, so schema depends on DataFrame shape and pandas inference.
- **No primary/foreign keys:** Relationships are convention-only.
- **Partial writes:** Listening events are appended before artist enrichment. If artist enrichment fails afterward, the fact rows can exist without matching artist rows.
- **Mixed DB paths:** Runtime, sample, reporting, and backup paths are inconsistent.
- **Case inconsistency:** `Artists_Info` and `artists_info` are used interchangeably.

### Silent failures and logging gaps

- **Broad exception on cursor read:** Any failure reading `Listening_History` is treated like first run, potentially hiding DB corruption or SQL errors.
- **Notebook wrapper reduces exceptions to strings:** `Run_Notebooks.ipynb` catches exceptions, emails the string, and does not produce structured error metadata.
- **Image generation failure is swallowed:** `Weekly_Analysis.ipynb` catches `fig.write_image` exceptions, prints a message, and continues.
- **No row-count assertions:** The pipeline does not validate expected new rows, duplicate rates, artist enrichment coverage, or report table writes.
- **No persistent structured logs:** Shell scripts print to stdout/stderr for cron redirection, but no application log file, JSON logs, or run table exists.

## 6. How to Run

### Required dependencies

The repository does not include a `requirements.txt` or lockfile. Based on imports and shell scripts, the runtime needs at least:

- Python 3
- `pandas`
- `numpy`
- `spotipy`
- `papermill`
- `nbformat`
- `nbconvert`
- `traitlets`
- `plotly`
- Static image export dependency for Plotly, historically Orca/Kaleido depending on environment
- `sqlite3` Python standard-library module and SQLite CLI for manual inspection
- AWS CLI for `aws_backup.sh`
- External local modules/scripts not included in this repo:
  - `/home/malcolm/EmailSender1/EmailSender.py`
  - `/home/malcolm/Spotify_Logger/Slope/plotSlope.py` for the bump/slope plot in `Weekly_Analysis.ipynb`

### Required files and environment

Expected by notebooks/scripts:

- Spotify credential pickle at `../credentials/spotify_creds.pkl` containing:
  - `SPOTIPY_CLIENT_ID`
  - `SPOTIPY_CLIENT_SECRET`
  - `SPOTIPY_REDIRECT_URI`
- Email password pickle at `../credentials/email_pw.pkl` for the runner failure email.
- Runtime database path: `data/listening_history.db`.
- Output directories that may need to exist:
  - `data/`
  - `run_notebooks/`
  - `images/`
  - `run_logs/` if using cron examples.

Assumption: The production host has a Spotify OAuth token cache already authorized for `user-read-recently-played`.

### Suggested local execution order

Do not run against a production DB until credentials and paths are corrected.

1. **Create required directories**
   ```bash
   mkdir -p data run_notebooks images run_logs
   ```

2. **Prepare credentials**
   - Prefer manually creating `../credentials/spotify_creds.pkl` and `../credentials/email_pw.pkl` to match current notebook paths.
   - `config_helper.py` cannot run unchanged because it contains placeholder syntax and writes to `data/`, not `../credentials/`.

3. **Bootstrap Spotify OAuth token**
   - Run the ingestion notebook interactively once if needed so Spotipy can complete the browser authorization flow.

4. **Run ingestion through the wrapper**
   ```bash
   papermill Run_Notebooks.ipynb run_notebooks/Run_Notebooks_local.ipynb -p db_location data/listening_history.db --no-progress-bar
   ```

5. **Or run the shell wrapper after editing paths**
   ```bash
   bash run_notebook.sh
   ```
   Before doing this locally, update or provide the hard-coded virtualenv path `/home/malcolm/main/bin/activate`.

6. **Inspect the DB**
   ```bash
   sqlite3 data/listening_history.db '.tables'
   sqlite3 data/listening_history.db '.schema Listening_History'
   ```

7. **Run reporting only after ingestion and artist metadata exist**
   ```bash
   papermill Weekly_Analysis.ipynb run_notebooks/Weekly_Analysis_local.ipynb -p look_back_days 14 --no-progress-bar
   ```
   Reporting currently depends on external modules and may fail without `/home/malcolm/EmailSender1/` and the slope plotting code.

8. **Set up cron after local validation**
   - Ingestion every two hours is suggested in `getting_started.txt`.
   - Weekly/monthly report scripts include cron examples in their comments.

## 7. Observability Gaps

### Missing logs

- No structured per-run log with run ID, start/end timestamps, status, DB path, cursor before/after, API result count, inserted row count, and error details.
- No persistent logs for artist enrichment counts, skipped artists, or failed Spotify API IDs.
- No clear log separation between ingestion, artist enrichment, reporting, image rendering, email, and backup.

### Missing metrics

- Number of recently played API items returned per run.
- Number of listening rows inserted per run.
- Number of duplicate candidate rows detected.
- Maximum/minimum `played_at` captured per run.
- Number of new artists found, fetched, inserted, skipped, or failed.
- Number of rows missing artist enrichment.
- Spotify API latency, error code, retry count, and rate-limit events.
- Report output row counts and email send status.
- Backup success/failure and backed-up file size.

### Missing validation checks

- Assert the runtime DB path exists and is the intended path before writes.
- Validate required tables and columns before ingestion/reporting.
- Validate credentials file paths and required credential keys before OAuth.
- Validate `after_ts` is numeric and monotonic enough for cursor usage.
- Check for duplicate listening events before and after insert.
- Check that `Artists_Info` coverage exists for all new `Listening_History.artist_id` values.
- Validate that output directories exist before writing notebooks/images.
- Validate that email/report dependencies are importable before running expensive analysis.

## 8. Suggested Next Steps (for fixing pipeline, NOT UI yet)

1. **Convert ingestion notebook logic into a Python module/CLI** while keeping the notebook as optional analysis documentation.
2. **Create a dependency file** (`requirements.txt` or `pyproject.toml`) and document supported Python version.
3. **Normalize configuration** into environment variables or one config file; remove hard-coded usernames, Gmail addresses, virtualenv paths, absolute `/home/malcolm/...` paths, and mixed credential locations.
4. **Create explicit SQLite migrations** for `Listening_History`, `Artists_Info`, report aggregate tables, and any playlist/source dimension.
5. **Add idempotency** with a stable event key. Prefer storing raw Spotify `played_at` plus `song_uri` and enforcing a unique constraint.
6. **Store raw payload audit fields** such as raw `played_at`, API cursor before/after, context type, context URI, and ingestion run ID.
7. **Replace fixed timezone math** with timezone-aware conversion.
8. **Add pagination/backfill logic** for recent plays and a separate controlled historical/backfill process where Spotify allows it.
9. **Make artist enrichment robust** with chunking, retry/backoff, upsert semantics, and clear matching on full artist URI vs bare ID.
10. **Repair or remove incomplete playlist code**; rename `playlist_id` to `context_uri` or add a parsed `context_type` column.
11. **Add a run log table** recording each ingestion/report/backup execution, status, counts, cursor ranges, DB path, and errors.
12. **Add automated tests** around parsers (`split_utc_time_str`, `get_recently_played`, artist parsing, playlist/source parsing) using saved sample Spotify API payloads.
13. **Separate reporting writes from ingestion** so failed reports cannot be confused with failed data collection.
14. **Only after the pipeline is stable**, consider UI/dashboard work on top of documented tables and validated data contracts.
