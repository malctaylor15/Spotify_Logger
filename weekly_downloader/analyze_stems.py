#!/usr/bin/env python3
"""Separate weekly tracks with Demucs and recommend the most interesting stems.

For each downloaded mp3 this script:
  1. runs Demucs locally (4 stems: vocals, drums, bass, other)
  2. scores every stem on loudness, dynamics, spectral variation and activity
  3. deletes the stem WAVs (they are large; Moises is the real destination)
  4. emails a recommendation of which stems are worth separating in Moises

Nothing is uploaded anywhere. Demucs runs entirely offline on this machine.

Usage:
    python3 analyze_stems.py --dry-run        # score only, no email
    python3 analyze_stems.py --report-only    # write the HTML report, no email
    python3 analyze_stems.py --send           # score and email the results
    python3 analyze_stems.py --keep-stems     # don't delete the WAVs
"""

import argparse
import json
import math
import shutil
import smtplib
import ssl
import subprocess
import sys
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
import soundfile as sf

import library_db

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DOWNLOADS_DIR = HERE / "downloads"
REPORTS_DIR = HERE / "reports"

DEMUCS_MODEL = "htdemucs"
STEM_ORDER = ["vocals", "drums", "bass", "other"]

# Email settings mirror the existing Weekly_Analysis flow.
GMAIL_LOGIN = "malctaylordev@gmail.com"
SENDER_EMAIL = "malctaylordev@gmail.com"
RECEIVER_EMAIL = "malctaylor15@gmail.com"
SMTP_PORT = 465
PASSWORD_PATHS = [
    REPO_ROOT / "data" / "email_pw.pkl",
    REPO_ROOT.parent / "credentials" / "email_pw.pkl",
]

# Composite weights. Loudness matters most, but a quiet, busy stem should still
# be able to win -- hence the substantial weight on spectral variation.
WEIGHTS = {
    "loudness": 0.35,
    "variation": 0.25,
    "activity": 0.20,
    "dynamics": 0.10,
    "entropy": 0.10,
}

FRAME = 2048
HOP = 1024
SILENCE_DBFS = -50.0      # frames quieter than this count as inactive
EPS = 1e-12


# --------------------------------------------------------------- audio io ---

def load_mono(path: Path, target_sr: int = 22050) -> tuple[np.ndarray, int]:
    """Load an audio file as mono float32, downsampling crudely for speed."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if target_sr and sr > target_sr:
        step = int(round(sr / target_sr))
        if step > 1:
            mono = mono[::step]
            sr = sr // step
    return mono, sr


def decode_to_wav(src: Path, dest: Path) -> bool:
    """Decode any audio file to wav via ffmpeg (soundfile can't read mp3)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-ac", "2", "-ar", "44100", str(dest)]
    return subprocess.run(cmd).returncode == 0


# ---------------------------------------------------------------- metrics ---

def db(x: float) -> float:
    return 20.0 * math.log10(max(float(x), EPS))


def frame_signal(x: np.ndarray) -> np.ndarray:
    """Split into overlapping frames (n_frames, FRAME)."""
    if len(x) < FRAME:
        x = np.pad(x, (0, FRAME - len(x)))
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx]


def stem_metrics(samples: np.ndarray) -> dict:
    """Raw (un-normalised) descriptors for one stem."""
    if samples.size == 0 or not np.any(np.isfinite(samples)):
        return {"rms_db": -120.0, "peak_db": -120.0, "crest_db": 0.0,
                "activity": 0.0, "flux": 0.0, "entropy": 0.0, "silent": True}

    rms = float(np.sqrt(np.mean(samples ** 2)))
    peak = float(np.max(np.abs(samples)))
    rms_db, peak_db = db(rms), db(peak)

    frames = frame_signal(samples)
    window = np.hanning(FRAME)
    frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
    frame_db = 20.0 * np.log10(np.maximum(frame_rms, EPS))

    # Activity: how much of the track this stem actually plays on.
    activity = float(np.mean(frame_db > SILENCE_DBFS))

    spec = np.abs(np.fft.rfft(frames * window, axis=1))

    # Spectral flux: frame-to-frame change, i.e. how much the stem moves.
    if spec.shape[0] > 1:
        norm = spec / (np.sum(spec, axis=1, keepdims=True) + EPS)
        flux = float(np.mean(np.sqrt(np.sum(np.diff(norm, axis=0) ** 2, axis=1))))
    else:
        flux = 0.0

    # Spectral entropy: how spread out the energy is (a proxy for complexity).
    loud = spec[frame_db > SILENCE_DBFS]
    if loud.shape[0] > 0:
        p = loud / (np.sum(loud, axis=1, keepdims=True) + EPS)
        ent = -np.sum(p * np.log2(p + EPS), axis=1)
        entropy = float(np.mean(ent) / math.log2(p.shape[1]))
    else:
        entropy = 0.0

    return {
        "rms_db": rms_db,
        "peak_db": peak_db,
        "crest_db": peak_db - rms_db,   # dynamic range proxy
        "activity": activity,
        "flux": flux,
        "entropy": entropy,
        "silent": rms_db < -60.0,
    }


def normalise(values: list[float], live: list[bool]) -> list[float]:
    """Scale to 0-1 using only the live (non-silent) stems to set the range.

    Silent stems would otherwise be extreme outliers -- an empty stem sits near
    -240 dB, which compresses every real stem into the top of the range and
    makes them all look 'dominant'. Silent stems are pinned to 0 instead.
    """
    live_vals = [v for v, keep in zip(values, live) if keep]
    if not live_vals:
        return [0.0] * len(values)
    lo, hi = min(live_vals), max(live_vals)
    if hi - lo < 1e-9:
        return [0.5 if keep else 0.0 for keep in live]
    out = []
    for v, keep in zip(values, live):
        if not keep:
            out.append(0.0)
        else:
            out.append(min(1.0, max(0.0, (v - lo) / (hi - lo))))
    return out


def score_stems(raw: dict[str, dict]) -> dict[str, dict]:
    """Turn raw per-stem metrics into a 0-100 composite, ranked within a song."""
    names = list(raw)
    live = [not raw[n]["silent"] and raw[n]["activity"] >= 0.02 for n in names]
    cols = {
        "loudness": normalise([raw[n]["rms_db"] for n in names], live),
        "variation": normalise([raw[n]["flux"] for n in names], live),
        "activity": normalise([raw[n]["activity"] for n in names], live),
        "dynamics": normalise([raw[n]["crest_db"] for n in names], live),
        "entropy": normalise([raw[n]["entropy"] for n in names], live),
    }
    out = {}
    for i, name in enumerate(names):
        parts = {k: cols[k][i] for k in WEIGHTS}
        composite = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS) * 100
        # A silent/near-empty stem is never interesting, whatever it scores.
        if raw[name]["silent"] or raw[name]["activity"] < 0.02:
            composite = 0.0
        out[name] = {**raw[name], **{f"n_{k}": v for k, v in parts.items()},
                     "score": round(composite, 1)}
    return out


def describe(name: str, m: dict) -> str:
    """One-line, human reason why this stem is (or isn't) interesting."""
    if m.get("description"):
        return m["description"]
    if m["score"] == 0.0:
        return "essentially absent from this track"
    bits = []
    if m["n_loudness"] > 0.75:
        bits.append("dominant in the mix")
    elif m["n_loudness"] < 0.25:
        bits.append("sits low in the mix")
    if m["n_variation"] > 0.75:
        bits.append("lots of movement")
    if m["n_dynamics"] > 0.75 and m["activity"] > 0.5:
        bits.append("wide dynamic range")
    if m["n_entropy"] > 0.75:
        bits.append("harmonically rich")
    if m["activity"] > 0.9:
        bits.append("plays throughout")
    elif m["activity"] < 0.4:
        bits.append(f"only active {m['activity']*100:.0f}% of the track")
    return ", ".join(bits) or "unremarkable on every measure"


# -------------------------------------------------------------- separation ---

def run_demucs(track: Path, out_dir: Path, model: str = DEMUCS_MODEL) -> Path | None:
    """Separate one track. Returns the folder holding the stem wavs."""
    cmd = [sys.executable, "-m", "demucs", "-n", model,
           "--out", str(out_dir), str(track)]
    if subprocess.run(cmd).returncode != 0:
        return None
    stem_dir = out_dir / model / track.stem
    return stem_dir if stem_dir.is_dir() else None


def split_title(stem_name: str) -> tuple[str, str]:
    """Recover (artist, title) from an 'Artist - Title' filename."""
    if " - " in stem_name:
        artist, title = stem_name.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem_name


def analyse_track(track: Path, workdir: Path, model: str, keep_stems: bool,
                  library=None, week_of: str = "", force: bool = False
                  ) -> dict | None:
    """Separate and score one track, reusing stored results when available."""
    print(f"\n=== {track.name} ===")
    artist, title = split_title(track.stem)

    if library is not None and not force:
        row = library_db.get_download(library, title, artist)
        if row and library_db.is_analysed(library, int(row["id"])):
            print("  already analysed - reusing stored metrics")
            for cached in library_db.load_week_results(library, row["week_of"]):
                if cached["track"] == track.stem:
                    return cached

    print("  separating (this takes a few minutes per track) ...")
    stem_dir = run_demucs(track, workdir, model)
    if stem_dir is None:
        print("  FAILED: demucs did not produce stems", file=sys.stderr)
        return None

    raw = {}
    for stem_file in sorted(stem_dir.glob("*.wav")):
        name = stem_file.stem
        samples, _ = load_mono(stem_file)
        raw[name] = stem_metrics(samples)
        print(f"  scored {name}")

    if not raw:
        print("  FAILED: no stem files found", file=sys.stderr)
        return None

    scored = score_stems(raw)
    ranked = sorted(scored.items(), key=lambda kv: kv[1]["score"], reverse=True)

    if not keep_stems:
        shutil.rmtree(stem_dir, ignore_errors=True)

    if library is not None:
        download_id = library_db.record_download(
            library, name=title, artist=artist,
            week_of=week_of or "unknown", file_path=track)
        library_db.record_analysis(
            library, download_id=download_id, name=title, artist=artist,
            week_of=week_of or "unknown", model=model,
            ranked_stems=ranked, describe=describe)

    return {"track": track.stem, "stems": dict(ranked),
            "ranking": [n for n, _ in ranked]}


# ----------------------------------------------------------------- report ---

def build_html(results: list[dict], week: str) -> str:
    rows = []
    for res in results:
        top = res["ranking"][0]
        top_m = res["stems"][top]
        stem_rows = "".join(
            f"<tr><td style='padding:4px 12px 4px 0'>{i}. <b>{n}</b></td>"
            f"<td style='padding:4px 12px 4px 0'>{m['score']}</td>"
            f"<td style='padding:4px 12px 4px 0'>{m['rms_db']:.1f} dB</td>"
            f"<td style='padding:4px 0;color:#555'>{describe(n, m)}</td></tr>"
            for i, (n, m) in enumerate(res["stems"].items(), 1))
        rows.append(f"""
        <div style="margin:0 0 28px 0">
          <h3 style="margin:0 0 2px 0">{res['track']}</h3>
          <p style="margin:0 0 8px 0;color:#444">
            Most interesting stem: <b>{top}</b> (score {top_m['score']}) &mdash;
            {describe(top, top_m)}
          </p>
          <table style="border-collapse:collapse;font-size:14px">
            <tr style="text-align:left;color:#666">
              <th style="padding-right:12px">Stem</th><th style="padding-right:12px">Score</th>
              <th style="padding-right:12px">Level</th><th>Why</th></tr>
            {stem_rows}
          </table>
        </div>""")

    return f"""<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;
        max-width:760px;color:#111">
      <h2 style="margin-bottom:4px">Weekly stem recommendations</h2>
      <p style="margin-top:0;color:#666">
        Week of {week} &middot; {len(results)} track(s) analysed locally with Demucs
        ({DEMUCS_MODEL}).</p>
      <p style="color:#444">Scores are relative <em>within</em> each song: they blend
        loudness, spectral variation, dynamic range, entropy and how much of the
        track the stem plays on. Upload the highlighted tracks to Moises to work
        with these stems.</p>
      {''.join(rows)}
      <p style="color:#888;font-size:12px">Generated by weekly_downloader/analyze_stems.py.
        Stems were scored locally and deleted; nothing was uploaded.</p>
    </body></html>"""


def build_text(results: list[dict], week: str) -> str:
    lines = [f"Weekly stem recommendations - week of {week}", ""]
    for res in results:
        top = res["ranking"][0]
        lines.append(f"{res['track']}")
        lines.append(f"  -> most interesting: {top} "
                     f"({res['stems'][top]['score']}) - "
                     f"{describe(top, res['stems'][top])}")
        for i, (n, m) in enumerate(res["stems"].items(), 1):
            lines.append(f"     {i}. {n:<8} score {m['score']:>5}  "
                         f"{m['rms_db']:>6.1f} dB  {describe(n, m)}")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ email ---

def load_email_password() -> str | None:
    import pickle
    for path in PASSWORD_PATHS:
        if path.exists():
            try:
                with path.open("rb") as fh:
                    return pickle.load(fh)["pw"]
            except (KeyError, EOFError, pickle.UnpicklingError):
                continue
    return None


def send_email(subject: str, text_body: str, html_body: str) -> bool:
    password = load_email_password()
    if not password:
        tried = "\n  ".join(str(p) for p in PASSWORD_PATHS)
        print(f"ERROR: no email password found. Looked in:\n  {tried}",
              file=sys.stderr)
        return False

    message = MIMEMultipart("alternative")
    message["From"] = SENDER_EMAIL
    message["To"] = RECEIVER_EMAIL
    message["Subject"] = subject
    message.attach(MIMEText(text_body, "plain"))
    message.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", SMTP_PORT, context=context) as server:
            server.login(GMAIL_LOGIN, password)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, message.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        print(f"ERROR: sending failed: {exc}", file=sys.stderr)
        return False
    print(f"Emailed {RECEIVER_EMAIL}")
    return True


# ------------------------------------------------------------------- main ---

def newest_week_folder() -> Path | None:
    weeks = sorted((p for p in DOWNLOADS_DIR.glob("week_of_*") if p.is_dir()),
                   key=lambda p: p.name)
    return weeks[-1] if weeks else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", type=Path, help="Week folder (default: newest)")
    parser.add_argument("--model", default=DEMUCS_MODEL, help="Demucs model name")
    parser.add_argument("--limit", type=int, help="Only analyse the first N tracks")
    parser.add_argument("--keep-stems", action="store_true",
                        help="Keep the stem WAVs instead of deleting them")
    parser.add_argument("--dry-run", action="store_true",
                        help="List the tracks that would be analysed, then stop")
    parser.add_argument("--report-only", action="store_true",
                        help="Write the HTML report but do not email it")
    parser.add_argument("--send", action="store_true",
                        help="Email the recommendations when finished")
    parser.add_argument("--library-db", type=Path, default=None,
                        help="Library database of downloads and stem metrics")
    parser.add_argument("--reanalyse", "--reanalyze", action="store_true",
                        dest="reanalyse",
                        help="Re-run Demucs even for already-analysed tracks")
    parser.add_argument("--from-db", metavar="WEEK", nargs="?", const="latest",
                        help="Rebuild the report/email from stored metrics "
                             "without any audio. Optionally name a week "
                             "(e.g. 2026-08-11); defaults to the newest.")
    args = parser.parse_args()

    library = library_db.connect(args.library_db)

    # Rebuild from stored metrics only -- no audio, no Demucs.
    if args.from_db:
        weeks = library_db.available_weeks(library)
        if not weeks:
            print("No analysed weeks in the library yet.", file=sys.stderr)
            return 1
        week = weeks[0] if args.from_db == "latest" else args.from_db
        results = library_db.load_week_results(library, week)
        if not results:
            print(f"No stored analysis for week {week}. "
                  f"Available: {', '.join(weeks)}", file=sys.stderr)
            return 1
        print(f"Rebuilt {len(results)} track(s) for week {week} from the library.")
        print("\n" + build_text(results, week))
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        html = build_html(results, week)
        (REPORTS_DIR / f"stems_{week}.html").write_text(html)
        print(f"Report written to {REPORTS_DIR / f'stems_{week}.html'}")
        if args.send:
            return 0 if send_email(f"Stem recommendations - week of {week}",
                                   build_text(results, week), html) else 1
        return 0

    folder = args.folder or newest_week_folder()
    if folder is None or not folder.is_dir():
        print("ERROR: no week folder found. Run download_weekly.py first.",
              file=sys.stderr)
        return 1

    tracks = sorted(folder.glob("*.mp3"))
    if args.limit:
        tracks = tracks[:args.limit]
    if not tracks:
        print(f"No mp3 files in {folder}")
        return 0

    print(f"{len(tracks)} track(s) in {folder.name}:")
    for t in tracks:
        print(f"  - {t.name}")

    if args.dry_run:
        print("\nDry run: nothing separated.")
        return 0

    if shutil.which("ffmpeg") is None:
        print("WARNING: ffmpeg not found; demucs may fail on mp3 input.\n")

    week = folder.name.replace("week_of_", "")
    results = []
    with tempfile.TemporaryDirectory(prefix="demucs_") as tmp:
        workdir = Path(tmp)
        for track in tracks:
            res = analyse_track(track, workdir, args.model, args.keep_stems,
                                library=library, week_of=week,
                                force=args.reanalyse)
            if res:
                results.append(res)

    if not results:
        print("\nNo tracks could be analysed.", file=sys.stderr)
        return 1

    print("\n" + build_text(results, week))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html = build_html(results, week)
    report_path = REPORTS_DIR / f"stems_{week}.html"
    report_path.write_text(html)
    (REPORTS_DIR / f"stems_{week}.json").write_text(json.dumps(results, indent=2))
    print(f"Report written to {report_path}")

    if args.send:
        subject = f"Stem recommendations - week of {week}"
        if not send_email(subject, build_text(results, week), html):
            return 1
    elif not args.report_only:
        print("\n(Not emailed. Re-run with --send to email the results.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
