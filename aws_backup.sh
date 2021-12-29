export path1="$(dirname "$0")"
cd $path1
echo "current working directory: "$PWD

export aws="/usr/local/bin/aws"
DATE=`date +%m-%d-%y`
new_file_name=listening_history_${DATE}.db
$aws s3 cp data/listening_history.db s3://do-mt-backups/Listening_History/${new_file_name}

#crontab -e
# 35 21 * * * bash /home/malcolm/Loggers/Spotify_Logger/aws_backup.sh

