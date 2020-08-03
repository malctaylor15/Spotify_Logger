export path1="$(dirname "$0")"
cd $path1
echo "current working directory: "$PWD

DATE=`date +%m-%d-%y`
new_file_name=listening_history_${DATE}.db
/home/pi/.local/bin/aws s3 cp data/listening_history.db s3://scraper-backup/listening_history/${new_file_name}
