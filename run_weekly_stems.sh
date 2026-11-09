#!/usr/bin/env bash
# Daily upload check + stem analysis. Audio is supplied by another machine;
# this job never invokes yt-dlp.
set -o pipefail
cd "$(dirname "$0")" || exit 1
echo "current working directory: $PWD"
echo "started: $(date)"

DATE=$(date +%m-%d-%Y)
LOG="run_logs/weekly_stems_${DATE}.log"
mkdir -p run_logs

TOP=${TOP:-8}

source .venv/bin/activate

{
  python weekly_downloader/weekly_upload_job.py --top "$TOP" --send
  analyse_exit_status=$?
  echo "weekly_upload_job.py exited $analyse_exit_status"
  exit $analyse_exit_status
} 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}

if [ "$status" -ne 0 ]; then
  echo "stem analysis exited $status - sending failure alert"
  python -m alerting --job "Weekly download + stems" --status "$status" --log "$LOG"
fi

echo "finished: $(date)"
exit $status

# crontab -e
# 15 4 * * * bash /home/malcolm/Spotify_Logger/run_weekly_stems.sh >> /home/malcolm/Spotify_Logger/run_logs/cron_stems.log 2>&1
