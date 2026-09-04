#!/usr/bin/env bash
# Weekly download + stem analysis. Logs to run_logs/ and emails an alert via
# alerting.py if the analysis step fails.
cd "$(dirname "$0")" || exit 1
echo "current working directory: $PWD"
echo "started: $(date)"

DATE=$(date +%m-%d-%Y)
LOG="run_logs/weekly_stems_${DATE}.log"
mkdir -p run_logs

# How many song breakdowns the email must contain. The downloader walks down
# the weekly chart until this many mp3s land, and the analyser tops the report
# up from earlier weeks if YouTube blocked everything.
TOP=${TOP:-2}
DAYS=${DAYS:-7}

source .venv/bin/activate

{
  python weekly_downloader/download_weekly.py --top "$TOP" --days "$DAYS"
  download_exit_status=$?
  if [ $download_exit_status -ne 0 ]; then
    echo "download_weekly.py exited $download_exit_status - analysing whatever landed anyway"
  fi

  # Runs regardless: --min-tracks guarantees a full report even on a bad
  # download week, so the email still goes out.
  python weekly_downloader/analyze_stems.py --send --min-tracks "$TOP"
  analyse_exit_status=$?
  echo "analyze_stems.py exited $analyse_exit_status"
  echo "download_weekly.py exited $download_exit_status"
  exit $analyse_exit_status
} 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}

if [ "$status" -ne 0 ]; then
  echo "stem analysis exited $status - sending failure alert"
  python -m alerting --job "Weekly download + stems" --status "$status" --log "$LOG"
elif grep -q "download_weekly.py exited [^0]" "$LOG"; then
  # The report still went out, but downloads failed - worth knowing about.
  echo "downloads failed although the report was sent - sending alert"
  python -m alerting --job "Weekly download (report still sent)" \
    --status 0 --log "$LOG" \
    --message "analyze_stems.py succeeded and the email was sent, but download_weekly.py failed. The report was topped up from earlier weeks."
fi

echo "finished: $(date)"
exit $status

# crontab -e
# 15 4 * * Sun bash /home/malcolm/Spotify_Logger/run_weekly_stems.sh >> /home/malcolm/Spotify_Logger/run_logs/cron_stems.log 2>&1
