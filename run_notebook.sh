export path1="$(dirname "$0")"
cd $path1
echo "current working directory: "$PWD

# File name set up
DATE=`date +%m-%d-%Y`
RN_FILENAME=Run_Notebooks_${DATE}.ipynb
LOCATION=run_notebooks/
RN_FILEPATH=$LOCATION$RN_FILENAME

# Use the repository-local environment created during setup.
source .venv/bin/activate


echo "Will be saving new notebook to: "$RN_FILEPATH
papermill get_latest_songs_prod.ipynb $RN_FILEPATH -p db_location data/listening_history.db --no-progress-bar
# Testing 
# papermill Run_Notebooks.ipynb $RN_FILEPATH -p db_location data/listening_history_qa.db 
# 
export papermill_exit_status=$?
if [ $papermill_exit_status -eq 0 ]
then
  echo "removing "$RN_FILEPATH
  rm $RN_FILEPATH
fi

exit $papermill_exit_status

# crontab -e
# 45 9 * * * * bash scripts/run_notebooks.sh >> /home/pi/tickets_pull/run_notebook.log 2>&1
