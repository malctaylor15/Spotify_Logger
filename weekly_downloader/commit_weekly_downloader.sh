#!/usr/bin/env bash
#
# Commit the weekly_downloader work and push to origin/master.
#
# Runs a safety check first: refuses to proceed if anything that looks like a
# credential, an audio file, or an environment backup has been staged.
#
# Usage:
#   bash commit_weekly_downloader.sh            # show what would be committed
#   bash commit_weekly_downloader.sh --push     # commit and push to master

set -uo pipefail

REPO="/Users/malcolmtaylor/python_related/Spotify_Logger"
cd "$REPO" || { echo "ERROR: repo not found at $REPO" >&2; exit 1; }

DO_PUSH=0
[[ "${1:-}" == "--push" ]] && DO_PUSH=1

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Current branch and remote"
git branch --show-current
git remote get-url origin

say "Working tree status"
git status --short

say "Staging the downloader work"
git add .gitignore
git add weekly_downloader/download_weekly.py
git add weekly_downloader/separate_stems.py
git add weekly_downloader/analyze_stems.py
git add weekly_downloader/upgrade_python.sh
git add weekly_downloader/README.md
git add weekly_downloader/WORKING_SETUP.md

say "Safety check on staged files"
STAGED="$(git diff --cached --name-only)"
echo "$STAGED" | sed 's/^/  /'

BAD="$(echo "$STAGED" | grep -Ei '(^|/)(data)/|\.pkl$|api_key|credential|password|_pw|\.mp3$|\.wav$|\.webm$|python_upgrade_backup' || true)"
if [[ -n "$BAD" ]]; then
    echo
    echo "REFUSING TO COMMIT - these staged paths look sensitive or unwanted:" >&2
    echo "$BAD" | sed 's/^/  /' >&2
    echo "Unstage them with: git restore --staged <path>" >&2
    exit 1
fi
echo "  OK - no credentials, audio, or backups staged."

if [[ $DO_PUSH -eq 0 ]]; then
    say "Dry run"
    echo "Nothing committed. Re-run with --push to commit and push to master."
    exit 0
fi

say "Committing"
git commit -F - <<'MSG'
Add weekly top-songs downloader with stem analysis

Adds a self-contained weekly_downloader/ tool that reads the existing
listening-history database, downloads the week's most-played songs from
YouTube, and recommends which separated stems are worth exploring.

- download_weekly.py: ranks the top N songs from the last 7 days (excluding
  anything ever played from the healing-music playlist) and fetches each as
  mp3 via yt-dlp. Includes a --doctor preflight for the yt-dlp/JS-runtime/EJS
  solver/ffmpeg chain, and automatic fallback across YouTube player clients
  since the default (android_vr) currently returns HTTP 403.

- analyze_stems.py: separates tracks locally with Demucs, scores each stem on
  a composite of loudness, spectral variation, activity, dynamics and entropy,
  deletes the WAVs, and emails recommendations via the existing Gmail setup.

- separate_stems.py: optional cloud stem separation through the Music AI API.

- upgrade_python.sh: rebuilds the virtualenvs on a newer Python, preserving
  the old environments and frozen package lists.

- WORKING_SETUP.md: records the verified-working configuration and the three
  distinct YouTube failure modes, each of which needs a different fix.

Audio output, reports and environment backups are gitignored.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Gy7JjWS5hphWmUFL2wbwLM
MSG

say "Pushing to origin/master"
git push origin master

say "Done"
git log --oneline -1
