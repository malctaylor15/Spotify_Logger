export path1="$(dirname "$0")"
cd $path1
echo "current working directory: "$PWD

# File name set up
DATE=`date +%m-%d-%Y`
RN_FILENAME=Weekly_Analysis_monthly_${DATE}.ipynb
LOCATION=run_notebooks/
RN_FILEPATH=$LOCATION$RN_FILENAME

source /home/malcolm/main/bin/activate
echo "Will be saving new notebook to: "$RN_FILEPATH
papermill Weekly_Analysis.ipynb $RN_FILEPATH -p look_back_days 30 --no-progress-bar

# Testing 
# papermill Run_Notebooks.ipynb $RN_FILEPATH -p db_location data/listening_history_qa.db 
# 
export papermill_exit_status=$?
if [ $papermill_exit_status -eq 0 ]
then
  echo "removing "$RN_FILEPATH
  rm $RN_FILEPATH
fi

exit 0

# crontab -e
# 30 8 * * Sun bash /home/malcolm/Spotify_Logger/run_analysis_monthly.sh >> /home/pi/Spotify_Logger/run_logs/run_analysis_monthly_notebook.log 2>&1