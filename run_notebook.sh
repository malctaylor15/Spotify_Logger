export path1="$(dirname "$0")"
cd $path1
echo "current working directory: "$PWD

# File name set up
DATE=`date +%m-%d-%Y`
RN_FILENAME=Run_Notebooks_${DATE}.ipynb
LOCATION=run_notebooks/
RN_FILEPATH=$LOCATION$RN_FILENAME

# Generic venv because requirements are minimal
source /home/malcolm/main/bin/activate


echo "Will be saving new notebook to: "$RN_FILEPATH
papermill Run_Notebooks.ipynb $RN_FILEPATH -p db_location data/listening_history.db --no-progress-bar
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
# 45 9 * * * * bash scripts/run_notebooks.sh >> /home/pi/tickets_pull/run_notebook.log 2>&1

