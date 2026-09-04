#!/usr/bin/env bash
# Weekly report. Logs to run_logs/ and emails an alert via alerting.py on failure.
cd "$(dirname "$0")" || exit 1
echo "current working directory: $PWD"
echo "started: $(date)"

DATE=$(date +%m-%d-%Y)
JOB="Weekly report"
LOG="run_logs/Weekly_Analysis_${DATE}.log"
RN_FILEPATH="run_notebooks/Weekly_Analysis_${DATE}.ipynb"
mkdir -p run_logs run_notebooks

# Use the repository-local environment created during setup.
source .venv/bin/activate

echo "Will be saving new notebook to: $RN_FILEPATH"
papermill Weekly_Analysis.ipynb "$RN_FILEPATH" \
  -p db_location data/listening_history.db --no-progress-bar 2>&1 | tee "$LOG"
# PIPESTATUS[0] is papermill's status; $? would be tee's.
status=${PIPESTATUS[0]}

if [ "$status" -eq 0 ]; then
  echo "removing $RN_FILEPATH"
  rm -f "$RN_FILEPATH"
else
  # The executed notebook is deliberately kept for inspection.
  echo "papermill exited $status - sending failure alert"
  python -m alerting --job "$JOB" --status "$status" --log "$LOG"
fi

echo "finished: $(date)"
exit $status

# crontab -e
# 30 8 * * Sun bash /home/malcolm/Spotify_Logger/run_email_notebook.sh >> /home/malcolm/Spotify_Logger/run_logs/cron_weekly.log 2>&1
