"""Configure and verify the local Spotify Web API connection."""

from __future__ import annotations

import argparse
import getpass
import os
import pickle
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
CREDENTIALS_PATH = DATA_DIR / "spotify_credentials.pkl"
TOKEN_CACHE_PATH = DATA_DIR / ".spotify_cache"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
REQUIRED_KEYS = (
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
)


def prompt_for_credentials() -> dict[str, str]:
    print("Paste values from your Spotify Developer Dashboard.")
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client secret (hidden): ").strip()
    redirect_uri = input(f"Redirect URI [{DEFAULT_REDIRECT_URI}]: ").strip()
    redirect_uri = redirect_uri or DEFAULT_REDIRECT_URI

    credentials = {
        "SPOTIPY_CLIENT_ID": client_id,
        "SPOTIPY_CLIENT_SECRET": client_secret,
        "SPOTIPY_REDIRECT_URI": redirect_uri,
    }
    missing = [key for key, value in credentials.items() if not value]
    if missing:
        raise SystemExit(f"Missing required value: {', '.join(missing)}")
    return credentials


def save_credentials(credentials: dict[str, str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with CREDENTIALS_PATH.open("wb") as handle:
        pickle.dump(credentials, handle)
    CREDENTIALS_PATH.chmod(0o600)
    print(f"Saved credentials to {CREDENTIALS_PATH}")


def load_credentials() -> dict[str, str]:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"No credentials found at {CREDENTIALS_PATH}. "
            "Run this script without --check-only first."
        )
    with CREDENTIALS_PATH.open("rb") as handle:
        credentials = pickle.load(handle)
    missing = [key for key in REQUIRED_KEYS if not credentials.get(key)]
    if missing:
        raise SystemExit(f"Credential file is missing: {', '.join(missing)}")
    return credentials


def verify_connection(credentials: dict[str, str]) -> None:
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError as exc:
        raise SystemExit(
            "Spotipy is not installed. Run: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc

    os.environ.update(credentials)
    auth_manager = SpotifyOAuth(
        scope="user-read-recently-played",
        cache_path=str(TOKEN_CACHE_PATH),
        open_browser=True,
    )
    spotify = spotipy.Spotify(auth_manager=auth_manager)
    spotify.current_user_recently_played(limit=1)
    print("Spotify connection verified. The token cache is ready for future runs.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save Spotify app credentials and complete OAuth sign-in."
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Use the saved credentials without asking for them again.",
    )
    args = parser.parse_args()

    if args.check_only:
        credentials = load_credentials()
    else:
        credentials = prompt_for_credentials()
        save_credentials(credentials)
    verify_connection(credentials)


if __name__ == "__main__":
    main()
