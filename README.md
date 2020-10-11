## Spotify Logger  

Repository to save spotify listening history to database 

This will allow for future analysis of different genre's, listening trends etc 


Spotify does not allow the user to see the entire listening history at once. 
So we use spotify's API to save recently listened history to sqlite database. We can set a scheduler to check every 2 hours. 

Once the data is captured, we can do analysis later on by tracking popular artists, genre trends, and potential lyrical trends. 
This is repository simply allows for the tracking of the spotify data. 

The listening_history.db file is an example of the output.


### Important Files List

* get_latest_songs_prod.ipynb is the notebook that makes the spotify API calls to get the latest songs a user has listened to and save them to a listening_history.db. This notebook is run by the Run Notebooks notebook. 

* Run_Notebook.ipynb is a wrapper notebook for the spotify calls notebook. If there are any errors in the Spotify calls notebook, this notebook will send an email to alert of the failures. This notebook is used in the command line script. 

* run_notebook.sh is the command line script that runs the Run_Notebook.ipynb in the proper environment. This script can be referenced by cron the scheduler. The last line of the file shows an example entry in cron. 

* config_helper.py is a script that can help set up the credentials in the correct place. You need a spotify client id, client scret key and redirect uri. We also need the email password saved in specific locations.  


