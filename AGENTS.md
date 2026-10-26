# Repository Guidelines

## Project Structure & Module Organization

Production workflows are notebook-driven. `get_latest_songs_prod.ipynb` ingests Spotify history into `data/listening_history.db`; `Weekly_Analysis.ipynb` creates weekly and monthly reports. Root `run_*.sh` scripts execute these notebooks through Papermill, while `alerting.py` provides shared failure notifications. `weekly_downloader/` contains the audio download, stem separation, analysis, and library-database tools. Database migrations live in `migrations/`, supporting documentation in `docs/`, and the vendored slope-graph library in `Slope/`. Read `DOCUMENTATION.md` before changing pipeline behavior and `Slope/VENDORED.md` before editing vendored code.

## Build, Test, and Development Commands

Use the repository-local environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-audio.txt  # optional audio stack
bash run_notebook.sh                 # ingest recent Spotify plays
bash run_email_notebook.sh           # generate the 14-day report
bash run_analysis_monthly.sh         # generate the 30-day report
bash run_weekly_stems.sh             # download and analyze weekly tracks
```

Run `.venv/bin/python weekly_downloader/download_weekly.py --doctor` before diagnosing downloads. Preview selection with `--dry-run`. Test reporting without email by running Papermill with `-p send_email False` as documented in `CLAUDE.md`.

## Coding Style & Naming Conventions

Follow existing Python conventions: four-space indentation, `snake_case` functions and variables, `UPPER_CASE` constants, and short module docstrings where behavior is non-obvious. Prefer `pathlib.Path`, parameterized SQL, and explicit CLI flags. Shell scripts should use `set -o pipefail`, quote paths, and preserve the producer status via `PIPESTATUS`. Keep notebook parameter cells tagged `parameters`. No formatter or linter is configured, so keep edits focused and consistent with nearby code.

## Testing Guidelines

There is no first-party automated test suite or coverage threshold. Validate notebook changes by executing them through Papermill and checking the output notebook for errors. Exercise CLI changes with safe modes such as `--dry-run`, `--doctor`, `--report-only`, or migration `--dry-run`. `Slope/test_slope.py` tests only the vendored library.

## Commit & Pull Request Guidelines

Recent history uses concise, imperative summaries (for example, `Realign the pipeline...` and `Vendor the Slope...`). Keep commits scoped to one coherent change. Pull requests should explain affected jobs and data/schema impact, list verification commands, link relevant issues, and include report screenshots when output changes. Never commit `.env`, `data/`, logs, generated notebooks, images, downloaded audio, or Spotify token caches.
