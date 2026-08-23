#!/usr/bin/env bash
#
# Commit the cleaned repo and the weekly_downloader work, then push to master.
#
# Run cleanup_repo.sh --apply FIRST. This script stages:
#   - the new weekly_downloader/ tooling
#   - the updated .gitignore
#   - stripped notebook outputs (tracked notebooks only)
#   - removal of the sample listening_history.db from tracking
#
# A safety gate refuses to commit if anything resembling a credential, audio
# file, or environment backup ends up staged.
#
# Usage:
#   bash weekly_downloader/commit_weekly_downloader.sh            # preview
#   bash weekly_downloader/commit_weekly_downloader.sh --push     # commit + push

set -uo pipefail

REPO="/Users/malcolmtaylor/python_related/Spotify_Logger"
cd "$REPO" || { echo "ERROR: repo not found at $REPO" >&2; exit 1; }

DO_PUSH=0
[[ "${1:-}" == "--push" ]] && DO_PUSH=1

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Branch and remote"
git branch --show-current
git remote get-url origin

# ------------------------------------------------------------ pre-checks ---
say "Pre-flight"

if [[ -n "$(find . -maxdepth 2 -name 'python_upgrade_backup_*' -o -maxdepth 2 -name '*.bak-*' 2>/dev/null)" ]]; then
    echo "  WARNING: environment backups still present - run cleanup_repo.sh --apply first" >&2
fi

BIG_NB=$(git ls-files '*.ipynb' | while IFS= read -r f; do
    [[ -f "$f" ]] && [[ $(wc -c < "$f") -gt 500000 ]] && echo "$f"
done)
if [[ -n "$BIG_NB" ]]; then
    echo "  WARNING: these notebooks still exceed 500KB (outputs not stripped?):" >&2
    echo "$BIG_NB" | sed 's/^/    /' >&2
else
    echo "  OK - no oversized notebooks."
fi

# --------------------------------------------------------------- staging ---
say "Staging"

# New tooling.
git add weekly_downloader/download_weekly.py
git add weekly_downloader/separate_stems.py
git add weekly_downloader/analyze_stems.py
git add weekly_downloader/library_db.py
git add weekly_downloader/import_reports.py
git add weekly_downloader/set_email_password.py
git add weekly_downloader/upgrade_python.sh
git add weekly_downloader/cleanup_repo.sh
git add weekly_downloader/commit_weekly_downloader.sh
git add weekly_downloader/README.md
git add weekly_downloader/WORKING_SETUP.md
git add .gitignore
git add DOCUMENTATION.md

# Modifications to already-tracked files (stripped notebooks, deletions).
git add -u

say "Staged files"
git diff --cached --name-status | sed 's/^/  /'

# ---------------------------------------------------------- safety check ---
say "Safety check"
# Only additions/modifications can leak something. Deletions of unwanted paths
# are exactly what we want, so they must not trip the gate.
STAGED="$(git diff --cached --name-only --diff-filter=ACMR)"
BAD="$(echo "$STAGED" | grep -Ei '(^|/)data/|\.pkl$|api_key|credential|password|_pw|\.mp3$|\.wav$|\.webm$|python_upgrade_backup|\.bak-' || true)"
if [[ -n "$BAD" ]]; then
    echo
    echo "REFUSING TO COMMIT - these staged paths look sensitive or unwanted:" >&2
    echo "$BAD" | sed 's/^/  /' >&2
    echo "Unstage with: git restore --staged <path>" >&2
    exit 1
fi
echo "  OK - no credentials, audio, or backups staged."

DIFFSTAT="$(git diff --cached --shortstat)"
echo "  $DIFFSTAT"

if [[ $DO_PUSH -eq 0 ]]; then
    say "Preview only"
    echo "  Nothing committed. Re-run with --push to commit and push to master."
    exit 0
fi

# ------------------------------------------------------------- commit -----
say "Committing"
git commit -F - <<'MSG'
Add weekly downloader with stem analysis; slim the repo

Adds a self-contained weekly_downloader/ tool that reads the existing
listening-history database, downloads the week's most-played songs from
YouTube, and recommends which separated stems are worth exploring. Also
trims the repository of generated content.

New tooling:

- download_weekly.py: ranks the top N songs from the last 7 days, excluding
  anything ever played from the healing-music playlist, and fetches each as
  mp3 via yt-dlp. Ships a --doctor preflight covering the yt-dlp version, JS
  runtime, EJS challenge solver and ffmpeg, plus automatic fallback across
  YouTube player clients: the current default (android_vr) returns HTTP 403,
  while web_embedded succeeds.

- analyze_stems.py: separates tracks locally with Demucs, scores each stem on
  a composite of loudness, spectral variation, activity, dynamics and spectral
  entropy, deletes the WAVs, and emails recommendations through the existing
  Gmail setup. Normalisation ignores silent stems, which would otherwise sit
  at roughly -240 dB and compress every real stem to the top of the scale.

- library_db.py: a small SQLite library at data/weekly_downloader.db so neither
  job repeats work. The downloads table records every song ever fetched, keyed
  on a normalised artist|title that tolerates case, accent and whitespace
  differences; the stem_analysis table stores rank, composite score and every
  raw and normalised measure behind it. Songs are checked against both the
  database and every week folder on disk before downloading, since a track can
  exist without a row. Because all email inputs are persisted, a past week's
  report can be regenerated with --from-db and no audio at all.

- separate_stems.py: optional cloud separation via the Music AI API.

- upgrade_python.sh / cleanup_repo.sh: rebuild the virtualenvs on a newer
  Python and clear the resulting backups, caches and stray files.

- WORKING_SETUP.md: records the verified-working configuration and the three
  distinct YouTube failure modes, each needing a different fix.

Documentation:

- DOCUMENTATION.md: a single index for the whole repository covering what the
  project does, every entry point, the database schema, credential locations,
  scheduled jobs and known rough edges, with links to each existing guide.
  The root README now points at it.

Repository cleanup:

- Notebook outputs stripped from tracked notebooks (~6.9 MB -> ~90 KB, 98.7%
  smaller). All code is preserved; only embedded plots and results are gone.
- listening_history.db untracked; it was a sample, and real data lives in the
  gitignored data/ directory.
- Audio output, generated reports and environment backups are gitignored.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Gy7JjWS5hphWmUFL2wbwLM
MSG

say "Pushing to origin/master"
git push origin master || { echo "Push failed." >&2; exit 1; }

say "Done"
git log --oneline -1
git status --short | head -10
