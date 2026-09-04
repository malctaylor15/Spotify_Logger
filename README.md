## Spotify Logger  

**[DOCUMENTATION.md](DOCUMENTATION.md) is the index for all documentation in
this repository** — what the project does, every entry point, the data model,
and links to each guide.

Repository to save spotify listening history to database 

This will allow for future analysis of different genre's, listening trends etc 


Spotify does not allow the user to see the entire listening history at once. 
So we use spotify's API to save recently listened history to sqlite database. We can set a scheduler to check every 2 hours. 

Once the data is captured, we can do analysis later on by tracking popular artists, genre trends, and potential lyrical trends. 
This is repository simply allows for the tracking of the spotify data. 

The runtime database is `data/listening_history.db` (gitignored).

**[DOCUMENTATION.md](DOCUMENTATION.md) is the index for all documentation here.**
[docs/pipeline_analysis.md](docs/pipeline_analysis.md) is an older deep-dive and
describes a credential layout that has since changed; where the two disagree,
DOCUMENTATION.md is authoritative.


### Important Files List

* get_latest_songs_prod.ipynb is the notebook that makes the spotify API calls to get the latest songs a user has listened to and save them to a listening_history.db. This notebook is run by the Run Notebooks notebook. 

* `run_notebook.sh` is the command line script that runs `get_latest_songs_prod.ipynb` in the `.venv` environment. It logs to `run_logs/`, and on failure emails an alert via `alerting.py` and keeps the executed notebook under `run_notebooks/`. It can be referenced by cron; the last line of the file shows an example entry.

* `alerting.py` is the shared failure alerting used by every scheduled job. Set `ALERT_DRY_RUN=1` to test a job's failure path without sending mail.

* `Weekly_Analysis.ipynb` builds the weekly/monthly email report. It takes `db_location`, `look_back_days` and `send_email` as papermill parameters — pass `-p send_email False` to build the report and its plots without sending anything.

* `config_helper.py` safely prompts for a Spotify client ID, client secret, and redirect URI, saves them to a gitignored `.env` file at the repo root, and completes the initial authorization (browser-based locally, or a paste-back URL flow on a headless server via `--remote`). Every notebook loads these same values with `dotenv.load_dotenv()`.

* `.env.sample` documents the three required variables (`SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`). Copy it to `.env` and fill in the two secret values — `.env` itself is gitignored and never committed.


### Environment

One `.venv` serves the whole repo — ingestion, reporting and the weekly
downloader. All four `run_*.sh` scripts activate it.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt        # ingestion + reporting
.venv/bin/python -m pip install -r requirements-audio.txt  # + weekly_downloader
```

`requirements-audio.txt` is split out only because Demucs pulls torch (~2GB).
The downloader also needs `ffmpeg` and `deno` on PATH, installed outside pip.

### Local Spotify setup (browser available)

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and select **Web API**.
2. In the app settings, add this redirect URI exactly: `http://127.0.0.1:8888/callback` (Spotify no longer accepts `localhost` for local callbacks).
3. Create the local Python environment and install the project dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. Start the private credential prompt and Spotify sign-in:

   ```bash
   .venv/bin/python config_helper.py
   ```

   Paste the app's Client ID and Client Secret when prompted. The secret is hidden while you type. Your browser will open so you can approve the single `user-read-recently-played` permission. Credentials are saved to `.env`; the resulting token cache is saved to the gitignored `data/.spotify_cache`.

5. Verify the saved connection later without re-entering credentials:

   ```bash
   .venv/bin/python config_helper.py --check-only
   ```

### Remote / headless setup (e.g. a droplet with no local browser)

Spotify's OAuth flow needs a browser to log in and a reachable redirect URI to receive the callback. On a server with no display and no SSH port-forward, use a redirect URI that's actually reachable — a domain that already resolves to the server's public IP over HTTPS (a bare IP will fail TLS certificate validation, so prefer a hostname), e.g. `https://ide.malctaylor15.com/`. The page it lands on doesn't need to do anything meaningful; you only need the URL from the address bar.

1. Create (or reuse) an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and add that exact redirect URI under the app's Redirect URIs.
2. Copy `.env.sample` to `.env` and fill in the real `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET`; set `SPOTIPY_REDIRECT_URI` to the reachable URL from step 1.
3. Run the helper with `--remote`:

   ```bash
   .venv/bin/python config_helper.py --check-only --remote   # if .env is already filled in
   # or, to also (re)enter credentials first:
   .venv/bin/python config_helper.py --remote
   ```

   It prints a Spotify login URL — open that in any browser on any device, log in, and approve access. You'll land on the redirect URI with `?code=...` in the address bar (the page content doesn't matter, even an error page is fine). Paste that full URL back into the prompt. The script exchanges the code for a token and caches it to `data/.spotify_cache`.
4. Once a valid token is cached, `config_helper.py --check-only --remote` (and the scheduled `run_notebook.sh` job) will reuse and silently refresh it — no further browser step needed until the refresh token itself is revoked.

### Pull recent listening history

Run the ingestion job from the repository root:

```bash
bash run_notebook.sh
```

The job writes recent plays and artist metadata to `data/listening_history.db`. It keeps a failed executed notebook under `run_notebooks/` for troubleshooting and removes the executed notebook after a successful run. Running it again only requests plays newer than the last successful pull.

For continuous collection, schedule `bash /Users/malcolmtaylor/python_related/Spotify_Logger/run_notebook.sh` to run every two hours. The computer must be awake and connected to the internet at the scheduled time.
