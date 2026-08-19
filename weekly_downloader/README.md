# Weekly Downloader

> **Working as of 2026-08-19.** See [WORKING_SETUP.md](WORKING_SETUP.md) for the
> verified configuration, what a healthy run looks like, and how to diagnose the
> three distinct failure modes if it breaks.

Self-contained tool that lives inside the Spotify_Logger repo. It reads the
repo's `data/listening_history.db`, finds the top 10 most-played songs from
the last 7 days, and uses **yt-dlp** to search YouTube for each song's
official music video, saving the audio as an mp3.

Songs that have ever been played from the healing-music playlist
(`spotify:playlist:0mnWqdNjnmb0AqNmXzQ0Vz`) are excluded from the ranking.

## Usage

```bash
cd /Users/malcolmtaylor/python_related/Spotify_Logger

# Preview the top 10 without downloading
python3 weekly_downloader/download_weekly.py --dry-run

# Download the top 10 from the last 7 days
python3 weekly_downloader/download_weekly.py

# Options
python3 weekly_downloader/download_weekly.py --top 5 --days 14 --db /path/to/other.db
```

Requires only the Python standard library, plus `yt-dlp` and `ffmpeg` on your
PATH.

## One-time setup

YouTube now requires yt-dlp to solve a JavaScript "signature" challenge. That
needs a JS runtime **and** the EJS solver scripts. Without both you get
`Signature solving failed`, then `Requested format is not available` because
only image formats survive.

```bash
brew install deno ffmpeg
python3 -m pip install -U "yt-dlp[default]"
```

`deno` is yt-dlp's recommended runtime and is used automatically. If you'd
rather use Node, the script detects it and passes `--js-runtimes node` for you.
`yt-dlp[default]` is what pulls in the `yt-dlp-ejs` package — plain
`pip install yt-dlp` is **not** enough.

### Watch out: which Python owns yt-dlp

`yt-dlp` on your PATH is often installed under a *different* interpreter than
the one running this script (conda env vs system vs Homebrew). Installing
`yt-dlp[default]` into the wrong one changes nothing, and the error message
looks identical. `--doctor` reads yt-dlp's own `--verbose` header rather than
importing the package locally, so it reports what yt-dlp actually sees, and it
prints the exact interpreter to install into.

If you'd rather not install the package at all, let yt-dlp fetch the solver
scripts at runtime:

```bash
python3 weekly_downloader/download_weekly.py --remote-ejs
```

That passes `--remote-components ejs:github` through to yt-dlp and sidesteps
the interpreter question entirely.

Verify everything at once:

```bash
python3 weekly_downloader/download_weekly.py --doctor
```

It reports the yt-dlp version, JS runtime, EJS solver, and ffmpeg, and exits
non-zero with a specific fix command for anything missing. The same check runs
automatically before each download so you fail fast instead of after ten
searches.

## Keeping yt-dlp current — important

YouTube regularly changes how it serves video, which breaks older yt-dlp
versions. The classic symptom is **every** download failing with:

```
ERROR: [youtube] <id>: The following content is not available on this app..
Watch on the latest version of YouTube
```

That is almost always a stale yt-dlp, not a problem with the song. Fix it:

```bash
python3 weekly_downloader/download_weekly.py --update-ytdlp
```

Or update it yourself:

```bash
python3 -m pip install --upgrade yt-dlp   # if installed via pip
brew upgrade yt-dlp                       # if installed via Homebrew
```

The script warns you when the installed version is more than 90 days old.

Note: forcing alternative player clients is **not** a workaround for signature
errors specifically. Those need the JS runtime and EJS solver. Client switching
is the fix for a *different* error — see below.

## HTTP Error 403 on the download

Different failure, different fix. If the log shows the search succeeding, a
format being chosen (`Downloading 1 format(s): 251`), and then:

```
ERROR: unable to download video data: HTTP Error 403: Forbidden
```

...then YouTube rejected the media URL itself. This happens when the player
client that produced the URL is required to present a **GVS PO Token** and
can't. It is not a search or selection problem — the right video was found.

The script retries automatically across player clients that are documented as
not requiring a PO Token, stopping at the first that works:

```
web_embedded -> default -> tv -> web_safari -> mweb
```

`web_embedded` leads because it is the verified-working client as of
2026-08-19, while yt-dlp's default (`android_vr`) 403'd on every song tested.

Pin a specific one, or set your own order:

```bash
python3 weekly_downloader/download_weekly.py --client web_embedded
python3 weekly_downloader/download_weekly.py --client tv --client web_embedded
```

If every client 403s, escalate in this order:

1. **PO Token provider plugin** (most robust, no account risk):

   ```bash
   python3 -m pip install -U bgutil-ytdlp-pot-provider
   ```

   yt-dlp then fetches tokens automatically and the restricted clients work.

2. **Cookies** — enables the `tv` client and bypasses most token checks:

   ```bash
   python3 weekly_downloader/download_weekly.py --cookies-from-browser safari
   ```

   Carries an account-ban risk; export from a private window, ideally from a
   throwaway account.

3. **Wait it out.** yt-dlp's current default client (`android_vr`) is 403-ing
   broadly right now — an open upstream site-bug, not something wrong with
   your setup. `--update-ytdlp` periodically will pick up the fix.

## Output

Downloads land in dated weekly folders:

```
weekly_downloader/
  downloads/
    download_archive.txt          # yt-dlp archive - prevents re-downloading
    week_of_2026-08-12/
      Fabolous - Make Me Better.mp3
      SZA - The Weekend.mp3
      ...
```

The `downloads/` folder is gitignored so audio files never enter the repo
history.

## Local stem analysis + email recommendations

`analyze_stems.py` separates each track locally with **Demucs**, scores every
stem, deletes the WAVs, and emails you which stems are worth working with in
Moises. Nothing is uploaded — separation is entirely offline.

```bash
python3 -m pip install -U demucs soundfile numpy

python3 weekly_downloader/analyze_stems.py --dry-run      # list tracks
python3 weekly_downloader/analyze_stems.py --report-only  # score + HTML, no email
python3 weekly_downloader/analyze_stems.py --send         # score + email
```

First run downloads the `htdemucs` model (~80 MB). Expect a few minutes per
track on CPU.

### How stems are scored

Each stem gets a 0–100 composite, **relative to the other stems in that same
song**, blending five measures:

| Measure | Weight | What it captures |
|---|---|---|
| Loudness (RMS) | 0.35 | How prominent the stem is in the mix |
| Spectral variation (flux) | 0.25 | How much it moves — busy vs static |
| Activity | 0.20 | Fraction of the track the stem actually plays on |
| Dynamics (crest factor) | 0.10 | Dynamic range |
| Spectral entropy | 0.10 | Harmonic richness / complexity |

The weighting deliberately lets a quieter but busier stem outrank a loud static
one. Stems that are silent or active for under 2% of the track score 0 outright
— Demucs often emits a near-empty stem, and it should never be recommended.

Normalisation uses only the non-silent stems to set the range. Including an
empty stem (~−240 dB) as an outlier compressed every real stem to the top of
the scale and labelled them all "dominant in the mix" — verified and fixed.

Each stem also gets a plain-language reason ("sits low in the mix, wide dynamic
range, harmonically rich") so the email is readable without the numbers.

### Email

Reuses the existing `Weekly_Analysis` setup: Gmail SMTP over SSL (port 465),
from `malctaylordev@gmail.com` to `malctaylor15@gmail.com`, password read from
`data/email_pw.pkl` (falling back to `../credentials/email_pw.pkl`). Sending
only happens with `--send`; every other mode writes the report to
`weekly_downloader/reports/` and stops.

## Stem separation via API (optional)

`separate_stems.py` sends the downloaded mp3s to **Music AI** (`api.music.ai`),
the developer API from the team behind Moises, and saves the separated stems
next to each track. Moises' consumer app has no public API — this is the
programmatic route.

```bash
# preview what would be uploaded (no key needed)
python3 weekly_downloader/separate_stems.py --dry-run

# separate the newest week's tracks
python3 weekly_downloader/separate_stems.py
```

### Setup

1. Create an account at <https://music.ai> and generate an API key.
2. Save it where the repo already gitignores it:

   ```bash
   mkdir -p data
   printf '%s' 'YOUR_KEY_HERE' > data/musicai_api_key.txt
   ```

   Or `export MUSIC_AI_API_KEY=...` for a single session.
3. Open your Music AI dashboard and copy the exact **workflow slug** for the
   4-stem split. Slugs are namespaced like `music-ai/stems-vocals-drums-bass-other`.
   If the default is wrong you'll get a clear "workflow not found" error — pass
   the right one with `--workflow`, then update `DEFAULT_WORKFLOW` in the script.

API usage consumes paid credits, which are **separate from a Moises app
subscription**. `--dry-run` costs nothing, so use it to confirm the track list
before spending anything.

### Output

```
downloads/week_of_2026-08-11/
    HARDY - Dog Years.mp3
    stems/
        HARDY - Dog Years/
            vocals.wav
            drums.wav
            bass.wav
            other.wav
```

Tracks that already have stems are skipped, so re-running is safe and free;
`--force` redoes them. Whatever stem names the workflow returns are saved as-is,
so switching to a 2-stem or 6-stem workflow needs no code change.

### Free local alternative

If you'd rather not pay per track, [Demucs](https://github.com/adefossez/demucs)
runs 4-stem separation offline on your Mac:

```bash
python3 -m pip install -U demucs
demucs "weekly_downloader/downloads/week_of_2026-08-11/HARDY - Dog Years.mp3"
```

Slower per track and needs no key or upload.

## Excluding more playlists

Add playlist URIs to `EXCLUDED_PLAYLIST_IDS` at the top of
`download_weekly.py`.

## Scheduling (optional)

To run automatically every Sunday at 9am, add to `crontab -e`:

```
0 9 * * 0 cd /Users/malcolmtaylor/python_related/Spotify_Logger && /usr/bin/python3 weekly_downloader/download_weekly.py >> weekly_downloader/downloads/weekly_download.log 2>&1
```
