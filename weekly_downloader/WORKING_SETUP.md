# Working setup — verified 2026-08-19

First fully successful run: **5/5 songs downloaded as mp3.** This file records
exactly what made it work, so a future breakage can be diffed against a known
-good state.

## The configuration that works

| Component | Value | Why it matters |
|---|---|---|
| yt-dlp | `2026.07.04` | Older versions are rejected outright by YouTube |
| JS runtime | `deno` | Solves the signature / "n" challenges |
| EJS solver | `yt_dlp_ejs` (via `yt-dlp[default]`) | Supplies the solver scripts deno runs |
| ffmpeg | installed | Converts the downloaded `.webm` to `.mp3` |
| Player client | **`web_embedded`** | The one client that isn't 403'd |
| Format | `251` (opus in webm) | Chosen automatically as best audio |

Install line that produced this state:

```bash
brew install deno ffmpeg
python3 -m pip install -U "yt-dlp[default]"
```

## What a successful download looks like

The two lines that confirm each piece is doing its job:

```
[youtube] ITc1RvfOdKw: Downloading web embedded client config
[youtube] [jsc:deno] Solving JS challenges using deno      <- solver active
[info] ITc1RvfOdKw: Downloading 1 format(s): 251
[download] Sleeping 5.00 seconds as required by the site... <- YouTube rate limit
[download] 100% of 4.30MiB
[ExtractAudio] Destination: .../HARDY - Dog Years.mp3       <- ffmpeg conversion
```

`[jsc:deno] Solving JS challenges` appearing is the single best signal that the
runtime + solver pair is healthy.

## Why `web_embedded` and not the default

yt-dlp's current default client is `android_vr`, and on this machine it 403'd
on **5 of 5** songs:

```
[youtube] cMD63TwzB1o: Downloading android vr player API JSON
[info] cMD63TwzB1o: Downloading 1 format(s): 251
ERROR: unable to download video data: HTTP Error 403: Forbidden
```

The search and format selection both succeeded — `cMD63TwzB1o` *is* the correct
SZA video. YouTube rejected the media URL itself, because that client is
expected to present a GVS PO Token and cannot. `web_embedded` is documented as
not requiring one, and it succeeded on the retry every time.

Because android_vr failed 100% of the time, `web_embedded` is now tried
**first** in `PLAYER_CLIENT_FALLBACKS`. This halves the work per song: one
search instead of two. yt-dlp's default is kept second so the script self-heals
if the situation reverses.

## The three errors, in the order they were hit

Each is a genuinely different failure. The distinction matters, because the fix
for one does nothing for the others.

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | `The following content is not available on this app` | yt-dlp was a year old (`2025.08.11`) | Upgrade yt-dlp |
| 2 | `Signature solving failed` → `Requested format is not available` | No JS runtime, no EJS solver | `brew install deno` + `pip install -U "yt-dlp[default]"` |
| 3 | `unable to download video data: HTTP Error 403` | Client needs a PO Token it can't supply | Switch player client to `web_embedded` |

Key trap: **error 2 is not fixed by switching player clients** (that was a wrong
turn taken during debugging — forcing `tv_simply` introduced a *new* PO Token
error on top of the real problem). **Error 3 is not fixed by upgrading.** Read
which of the three you have before acting.

Second trap: `pip install yt-dlp` does **not** install the solver. The
`[default]` extra is required. And it must go into the interpreter that
actually runs `yt-dlp`, which is often not the active venv — `--doctor` reads
yt-dlp's own `--verbose` header rather than importing locally, so it reports
what yt-dlp truly sees.

## If it breaks again

```bash
python3 weekly_downloader/download_weekly.py --doctor
```

Checks version, runtime, solver, and ffmpeg, printing a specific fix command
for anything missing. Then, by symptom:

- **All songs 403** → the working client changed. Try others:
  `--client tv`, `--client web_safari`, `--client mweb`. Update the table above
  when a new one wins.
- **Every client 403s** → install a PO Token provider:
  `python3 -m pip install -U bgutil-ytdlp-pot-provider`
- **`Signature solving failed` returns** → deno or the EJS package went missing;
  re-run the install line at the top.
- **Nothing works** → `--update-ytdlp`. YouTube-side breakage is usually fixed
  upstream within days.

## Moises stem separation — what works, tested 2026-08-19

Both automated upload paths on `studio.moises.ai` were probed directly and
**neither can be driven by browser automation**:

| Path | Result |
|---|---|
| **Local files** tab | No `<input type="file">` exists in the DOM — not in shadow roots, not in any of the 6 iframes. The input is created only on clicking `+`, which opens a **native macOS file picker** that browser automation cannot see or control. |
| **Cloud storage** tab | Accepts a URL, but YouTube links are rejected: *"Unfortunately, the URLs of this streaming service are not accepted."* Only direct/public file URLs work. |

The library still contains an old YouTube-derived entry
(`... [hxcVORJ4SB8]`), so YouTube import worked at some point and was
withdrawn. Don't assume it will come back.

**Therefore: drag and drop is the method.** The dropzone accepts **up to 20
files at once**, so a whole week goes in as a single gesture, uses the existing
Premium plan, and costs nothing extra.

```
Upload page:  https://studio.moises.ai/upload/split/3
Tracks:       weekly_downloader/downloads/week_of_<date>/
Open folder:  open weekly_downloader/downloads/week_of_2026-08-11
```

Supported input formats: MP3, WAV, FLAC, M4A, MP4, MOV, WMA.

Alternatives, if the manual step ever becomes annoying:

- `separate_stems.py` — the Music AI API. Fully automated; needs an API key and
  paid credits, which are **separate from a Moises Premium subscription**.
- **Demucs** — free, offline, scriptable: `python3 -m pip install -U demucs`.
- Hosting the mp3s at public URLs would make the Cloud storage tab automatable,
  but it exposes the files publicly and adds a hosting step.

## Notes and gotchas

- **Rate limiting is real.** YouTube injects `Sleeping 5.00 seconds` between
  downloads. Ten songs takes roughly a minute of pure waiting. Don't mistake it
  for a hang, and don't parallelise — the guest limit is ~300 videos/hour.
- **`.webm` → `.mp3` leaves no original.** yt-dlp deletes the source after
  conversion. Pass `-k` to yt-dlp if you ever want to keep it.
- **The download archive prevents re-downloads.** `downloads/download_archive.txt`
  records completed video IDs, so re-running skips work already done. Delete a
  line to force one song to re-download.
- **Cookies were never needed.** Anonymous access is sufficient at this volume,
  and using an account carries a ban risk. `--cookies-from-browser` exists as a
  last resort only.
