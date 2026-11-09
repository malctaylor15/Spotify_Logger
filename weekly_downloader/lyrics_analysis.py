"""Render reviewed, source-backed lyric interpretations without storing lyrics.

Retrieval and model calls are intentionally outside this free-source pilot.
Only explicitly supported derived fields survive into report artifacts.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import unicodedata
from urllib.parse import urlsplit

PROMPT_VERSION = "lyrics-themes-v1"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "data" / "lyrics_analysis.json"
HEADING = "Stories and themes in your top songs"
STATUSES = {"analyzed", "partial", "unavailable", "unverified", "instrumental"}


def identity(name: str, artist: str) -> str:
    """Keep edition/remix/feature wording; never merge covers by title alone."""
    parts = [" ".join(unicodedata.normalize("NFKC", v).casefold().split())
             for v in (artist, name)]
    return json.dumps(parts, ensure_ascii=False)


def safe_url(value: object) -> str:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username else ""
    except ValueError:
        return ""


def tags(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(v.strip().casefold() for v in value
                             if isinstance(v, str) and 0 < len(v.strip()) <= 80))[:limit]


def reviewed_entry(raw: dict) -> dict:
    """Validate provenance and discard unsupported fields (including raw lyrics)."""
    status = raw.get("status", "unavailable")
    if status not in STATUSES:
        raise ValueError("unknown analysis status")
    sources = []
    for source in raw.get("sources", []):
        url = safe_url(source.get("url"))
        if not url:
            continue
        sources.append({"label": str(source.get("label", "Source")), "url": url,
                        "kind": str(source.get("kind", "metadata")),
                        "completeness": str(source.get("completeness", "unavailable")),
                        "accessed_on": str(source.get("accessed_on", ""))})
    summary = str(raw.get("summary", "")).strip()
    version = raw.get("prompt_version")
    verified = raw.get("version_verified") is True
    has_lyrics = any(s["kind"] == "lyrics" and s["completeness"] in {"complete", "partial"}
                     for s in sources)
    if status in {"analyzed", "partial"}:
        if version != PROMPT_VERSION or not has_lyrics or not 40 <= len(summary.split()) <= 60:
            raise ValueError("interpretation needs current prompt, inspected lyrics and 40–60 words")
        if status == "analyzed" and (not verified or not any(
                s["kind"] == "lyrics" and s["completeness"] == "complete" for s in sources)):
            raise ValueError("complete analysis needs verified version and complete lyrics")
    elif status == "instrumental":
        if not verified or not sources:
            raise ValueError("instrumental classification needs verified source")
        summary = ""
    else:
        summary = ""
    return {"status": status, "summary": summary,
            "themes": tags(raw.get("themes"), 3) if status in {"analyzed", "partial"} else [],
            "tones": tags(raw.get("tones"), 2) if status in {"analyzed", "partial"} else [],
            "sources": sources, "version_verified": verified,
            "note": str(raw.get("note", "")), "prompt_version": str(version or PROMPT_VERSION)}


def analysis_signature(tracks: list[dict]) -> str:
    # Listening weights are recalculated separately, so summaries can be reused.
    payload = [{k: v for k, v in t.items() if k not in {"plays", "rank"}} for t in tracks]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_report(songs: list[dict], week: str, cache_path: Path = DEFAULT_CACHE) -> dict:
    warnings = []
    try:
        cache = json.loads(cache_path.read_text())
        if not isinstance(cache, dict) or not isinstance(cache.get("entries"), list):
            raise ValueError("expected an object containing entries")
    except FileNotFoundError:
        cache = {"entries": []}
    except (OSError, ValueError) as exc:
        cache = {"entries": []}
        warnings.append(f"Lyric analysis cache unavailable: {type(exc).__name__}; other report sections are unaffected.")
    by_id = {}
    duplicates = set()
    for raw in cache["entries"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not isinstance(raw.get("artist_name"), str):
            continue
        key = identity(raw["name"], raw["artist_name"])
        if key in by_id:
            duplicates.add(key)
        by_id[key] = raw
    tracks = []
    for rank, song in enumerate(songs, 1):
        key = identity(song["name"], song["artist_name"])
        raw = by_id.get(key, {"note": "No reviewed lyric interpretation is available yet."})
        try:
            if key in duplicates:
                raise ValueError("duplicate cached identities need review")
            entry = reviewed_entry(raw)
        except (ValueError, TypeError, AttributeError) as exc:
            entry = reviewed_entry({"note": f"Cached interpretation needs review: {exc}."})
        tracks.append({"track_id": key, "rank": rank, "name": song["name"],
                       "artist_name": song["artist_name"], "plays": song["plays"], **entry})
    analyzed = [t for t in tracks if t["status"] == "analyzed"]
    covered = sum(t["plays"] for t in analyzed)
    total = sum(t["plays"] for t in tracks)
    counts = Counter()
    supporters = {}
    for track in analyzed:
        for theme in track["themes"]:
            counts[theme] += track["plays"]
            supporters.setdefault(theme, []).append(track)
    themes = [{"theme": theme, "plays": n, "share": n / covered,
               "songs": [t["track_id"] for t in supporters[theme]]}
              for theme, n in counts.most_common()] if covered else []
    common = [t for t in themes if len(t["songs"]) >= 2]
    if common:
        synthesis = "Shared themes: " + "; ".join(
            f"{t['theme']} in " + ", ".join(
                f"{s['artist_name']} — {s['name']}" for s in supporters[t["theme"]])
            for t in common) + ". Each song's interpretation below explains its particular perspective."
    elif len(analyzed) >= 2:
        synthesis = "No strong shared theme across the reviewed songs. The individual interpretations show their different concerns."
    else:
        synthesis = "Not enough fully verified lyric analyses to compare themes this week."
    signature = analysis_signature(tracks)
    # A reviewed narrative is usable only for the exact week and analysis set.
    weekly = cache.get("weekly_syntheses", {})
    narrative = weekly.get(week, {}) if isinstance(weekly, dict) else {}
    if (isinstance(narrative, dict) and narrative.get("analysis_signature") == signature
            and isinstance(narrative.get("text"), str) and narrative["text"].strip()):
        synthesis = narrative["text"].strip()
    return {"prompt_version": PROMPT_VERSION, "week_of": week, "tracks": tracks,
            "analysis_signature": signature, "synthesis": synthesis,
            "coverage": {"analyzed_songs": len(analyzed), "total_songs": len(tracks),
                         "analyzed_plays": covered, "total_plays": total},
            "theme_prevalence": themes, "warnings": warnings}


def overview(report: dict) -> list[str]:
    c = report["coverage"]
    return [report["synthesis"],
            f"Lyric coverage: {c['analyzed_songs']}/{c['total_songs']} songs; "
            f"{c['analyzed_plays']}/{c['total_plays']} top-song plays fully analyzed. "
            "Partial, unverified, unavailable and instrumental entries are excluded from theme percentages.",
            "Themes by plays in fully analyzed songs: " + ("; ".join(
                f"{t['theme']}: {t['plays']} plays ({t['share']:.0%})"
                for t in report["theme_prevalence"]) or "not available") + ". "
            "Theme tags overlap; percentages need not total 100%.",
            "These are interpretations of the songs, not conclusions about your circumstances or reasons for listening.",
            *report["warnings"]]


def track_lines(track: dict) -> list[str]:
    lines = [f"#{track['rank']} {track['artist_name']} — {track['name']} · {track['plays']} plays",
             f"Status: {track['status']}"]
    if track["summary"]:
        lines.append(track["summary"])
    if track["themes"]:
        lines.append("Themes: " + ", ".join(track["themes"]))
    if track["tones"]:
        lines.append("Tone: " + ", ".join(track["tones"]))
    if track["note"]:
        lines.append(track["note"])
    return lines


def render_html(report: dict) -> str:
    parts = [f"<h3>{HEADING}</h3>"]
    parts.extend(f"<p>{html.escape(line)}</p>" for line in overview(report))
    for track in report["tracks"]:
        lines = track_lines(track)
        parts.append("<div style='border-top:1px solid #ddd;padding:12px 0'>")
        parts.append(f"<h4>{html.escape(lines[0])}</h4>")
        parts.extend(f"<p>{html.escape(line)}</p>" for line in lines[1:])
        for source in track["sources"]:
            url = safe_url(source["url"])
            if url:
                label = f"{source['label']} ({source['kind']}; {source['completeness']})"
                parts.append(f'<p><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></p>')
        if not track["sources"]:
            parts.append("<p>Source: unavailable</p>")
        parts.append("</div>")
    return "\n".join(parts)


def render_text(report: dict) -> str:
    lines = [HEADING, "", *overview(report)]
    for track in report["tracks"]:
        lines.extend(["", *track_lines(track)])
        lines.extend(f"Source: {s['label']} ({s['kind']}; {s['completeness']}) — {s['url']}"
                     for s in track["sources"])
        if not track["sources"]:
            lines.append("Source: unavailable")
    return "\n".join(lines)
