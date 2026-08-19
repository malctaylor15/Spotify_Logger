#!/usr/bin/env python3
"""Send downloaded tracks to Music AI (Moises' developer API) for stem separation.

Companion to download_weekly.py. That script fills a weekly folder with mp3s;
this one uploads each track, runs a stem-separation workflow, and saves the
resulting stems alongside the original.

Pipeline per track:
    GET  /v1/upload            -> signed uploadUrl + downloadUrl
    PUT  <uploadUrl>           -> the mp3 bytes
    POST /v1/job               -> {name, workflow, params:{inputUrl}}
    GET  /v1/job/<id>          -> poll until SUCCEEDED / FAILED
    GET  <each result url>     -> save the stems

Output layout:
    downloads/week_of_2026-08-11/
        HARDY - Dog Years.mp3
        stems/
            HARDY - Dog Years/
                vocals.wav
                drums.wav
                bass.wav
                other.wav

Usage:
    python3 separate_stems.py --dry-run          # show what would be sent
    python3 separate_stems.py                    # process the newest week
    python3 separate_stems.py --folder <path>    # a specific week
    python3 separate_stems.py --workflow my-slug # override the workflow

Credentials (never hardcode the key):
    data/musicai_api_key.txt   under the repo, gitignored
    or  export MUSIC_AI_API_KEY=...
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DOWNLOADS_DIR = HERE / "downloads"
KEY_FILE = REPO_ROOT / "data" / "musicai_api_key.txt"

API_BASE = "https://api.music.ai/v1"

# Workflow slug. Music AI workflows are namespaced, and you can build your own
# in the dashboard -- confirm the exact slug there before relying on this.
DEFAULT_WORKFLOW = "music-ai/stems-vocals-drums-bass-other"

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 900          # 15 minutes per track


# ------------------------------------------------------------- credentials ---

def load_api_key() -> str | None:
    """Read the API key from the environment or the gitignored key file."""
    key = os.environ.get("MUSIC_AI_API_KEY", "").strip()
    if key:
        return key
    if KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
        if key:
            return key
    return None


# ---------------------------------------------------------------- http ------

def api_request(method: str, url: str, api_key: str,
                body: dict | None = None, timeout: int = 60) -> dict:
    """Make a JSON request against the Music AI API."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", api_key)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def put_file(url: str, path: Path, timeout: int = 600) -> None:
    """Upload a file to a signed URL."""
    payload = path.read_bytes()
    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("Content-Length", str(len(payload)))
    with urllib.request.urlopen(req, timeout=timeout):
        pass


def download(url: str, dest: Path, timeout: int = 600) -> None:
    """Save a URL to disk."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as resp, \
            open(dest, "wb") as fh:
        while chunk := resp.read(1 << 16):
            fh.write(chunk)


# ------------------------------------------------------------- separation ---

def separate_track(track: Path, out_dir: Path, api_key: str,
                   workflow: str) -> tuple[bool, str]:
    """Run one track through the API. Returns (ok, message)."""
    try:
        slots = api_request("GET", f"{API_BASE}/upload", api_key)
        upload_url = slots["uploadUrl"]
        input_url = slots["downloadUrl"]
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError) as exc:
        return False, f"could not get an upload slot: {exc}"

    try:
        print(f"     uploading ({track.stat().st_size / 1e6:.1f} MB) ...")
        put_file(upload_url, track)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return False, f"upload failed: {exc}"

    try:
        job = api_request("POST", f"{API_BASE}/job", api_key, body={
            "name": f"stems: {track.stem}",
            "workflow": workflow,
            "params": {"inputUrl": input_url},
        })
        job_id = job["id"]
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="ignore")[:300]
        if exc.code in (401, 403):
            return False, f"auth rejected ({exc.code}). Check the API key."
        if exc.code == 404:
            return False, (f"workflow {workflow!r} not found. Copy the exact "
                           f"slug from your Music AI dashboard and pass it "
                           f"with --workflow.")
        return False, f"job creation failed ({exc.code}): {detail}"
    except (urllib.error.URLError, KeyError) as exc:
        return False, f"job creation failed: {exc}"

    print(f"     job {job_id} queued, waiting ...")
    deadline = time.monotonic() + POLL_TIMEOUT_S
    result = None
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_S)
        try:
            status_doc = api_request("GET", f"{API_BASE}/job/{job_id}", api_key)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            return False, f"status poll failed: {exc}"
        status = status_doc.get("status")
        if status == "SUCCEEDED":
            result = status_doc.get("result") or {}
            break
        if status == "FAILED":
            return False, f"job failed: {status_doc.get('error', 'no detail')}"
    else:
        return False, f"timed out after {POLL_TIMEOUT_S}s"

    if not result:
        return False, "job succeeded but returned no stems"

    # The result is a {stem_name: url} map; save whatever the workflow produced
    # rather than assuming a fixed set of stem names.
    saved = 0
    for stem_name, url in result.items():
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        suffix = Path(url.split("?")[0]).suffix or ".wav"
        dest = out_dir / f"{stem_name}{suffix}"
        try:
            download(url, dest)
            saved += 1
            print(f"     saved {dest.name}")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            print(f"     WARNING: could not save {stem_name}: {exc}",
                  file=sys.stderr)

    if saved == 0:
        return False, "no stems could be downloaded"
    return True, f"{saved} stems"


# ------------------------------------------------------------------ main ----

def newest_week_folder() -> Path | None:
    weeks = sorted((p for p in DOWNLOADS_DIR.glob("week_of_*") if p.is_dir()),
                   key=lambda p: p.name)
    return weeks[-1] if weeks else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path,
                        help="Week folder to process (default: newest)")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW,
                        help=f"Music AI workflow slug (default: {DEFAULT_WORKFLOW})")
    parser.add_argument("--limit", type=int,
                        help="Only process the first N tracks")
    parser.add_argument("--dry-run", action="store_true",
                        help="List the tracks that would be sent, then stop")
    parser.add_argument("--force", action="store_true",
                        help="Re-separate tracks that already have stems")
    args = parser.parse_args()

    folder = args.folder or newest_week_folder()
    if folder is None or not folder.is_dir():
        print("ERROR: no week folder found. Run download_weekly.py first.",
              file=sys.stderr)
        return 1

    tracks = sorted(folder.glob("*.mp3"))
    if not tracks:
        print(f"No mp3 files in {folder}")
        return 0

    stems_root = folder / "stems"
    pending = []
    for track in tracks:
        out_dir = stems_root / track.stem
        if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
            print(f"  skip (already separated): {track.name}")
            continue
        pending.append((track, out_dir))

    if args.limit:
        pending = pending[:args.limit]

    if not pending:
        print("Nothing to do - every track already has stems. "
              "Use --force to redo them.")
        return 0

    print(f"\n{len(pending)} track(s) to separate from {folder.name}:")
    for track, _ in pending:
        print(f"  - {track.name}")
    print(f"\nWorkflow: {args.workflow}")

    if args.dry_run:
        print("\nDry run: nothing uploaded.")
        return 0

    api_key = load_api_key()
    if not api_key:
        print(f"""
ERROR: no Music AI API key found.

  1. Create an account and generate a key at https://music.ai
  2. Save it to:  {KEY_FILE}
        mkdir -p "{KEY_FILE.parent}"
        printf '%s' 'YOUR_KEY_HERE' > "{KEY_FILE}"
     (data/ is gitignored, so the key stays out of the repo)

  Or export it for one session:
        export MUSIC_AI_API_KEY=YOUR_KEY_HERE

  Note: uploads consume paid API credits, which are separate from a
  Moises app subscription.
""".rstrip(), file=sys.stderr)
        return 1

    failures = []
    for i, (track, out_dir) in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] {track.name}")
        out_dir.mkdir(parents=True, exist_ok=True)
        ok, message = separate_track(track, out_dir, api_key, args.workflow)
        if ok:
            print(f"     done - {message}")
        else:
            print(f"     FAILED - {message}", file=sys.stderr)
            failures.append(f"{track.name}: {message}")
            # Leave no empty dir behind, so a rerun retries this track.
            if not any(out_dir.iterdir()):
                out_dir.rmdir()

    print(f"\nStems are in: {stems_root}")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
