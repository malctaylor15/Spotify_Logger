## Spotify Logger  

Repository to save spotify listening history to database 

This will allow for future analysis of different genre's, listening trends etc 


Spotify does not allow the user to see the entire listening history at once. 
So we use spotify's API to save recently listened history to sqlite database. We can set a scheduler to check every 2 hours. 

Once the data is captured, we can do analysis later on by tracking popular artists, genre trends, and potential lyrical trends. 
This is repository simply allows for the tracking of the spotify data. 

The listening_history.db file is an example of the output.

For a detailed legacy pipeline walkthrough, database map, run instructions, and risk analysis, see [docs/pipeline_analysis.md](docs/pipeline_analysis.md).


### Important Files List

* get_latest_songs_prod.ipynb is the notebook that makes the spotify API calls to get the latest songs a user has listened to and save them to a listening_history.db. This notebook is run by the Run Notebooks notebook. 

* Run_Notebook.ipynb is a wrapper notebook for the spotify calls notebook. If there are any errors in the Spotify calls notebook, this notebook will send an email to alert of the failures. This notebook is used in the command line script. 

* run_notebook.sh is the command line script that runs the Run_Notebook.ipynb in the proper environment. This script can be referenced by cron the scheduler. The last line of the file shows an example entry in cron. 

* `config_helper.py` safely prompts for a Spotify client ID, client secret, and redirect URI, saves them under the ignored `data/` directory, and completes the initial browser authorization.


### Local Spotify setup

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and select **Web API**.
2. In the app settings, add this redirect URI exactly: `http://127.0.0.1:8888/callback` (Spotify no longer accepts `localhost` for local callbacks).
3. Create the local Python environment and install the project dependencies:

   ```bash
   /opt/homebrew/bin/python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. Start the private credential prompt and Spotify sign-in:

   ```bash
   .venv/bin/python config_helper.py
   ```

   Paste the app's Client ID and Client Secret when prompted. The secret is hidden while you type. Your browser will open so you can approve the single `user-read-recently-played` permission. Credentials and the resulting token cache stay under the gitignored `data/` directory.

5. Verify the saved connection later without re-entering credentials:

   ```bash
   .venv/bin/python config_helper.py --check-only
   ```

### Pull recent listening history

Run the ingestion job from the repository root:

```bash
bash run_notebook.sh
```

The job writes recent plays and artist metadata to `data/listening_history.db`. It keeps a failed executed notebook under `run_notebooks/` for troubleshooting and removes the executed notebook after a successful run. Running it again only requests plays newer than the last successful pull.

For continuous collection, schedule `bash /Users/malcolmtaylor/python_related/Spotify_Logger/run_notebook.sh` to run every two hours. The computer must be awake and connected to the internet at the scheduled time.
