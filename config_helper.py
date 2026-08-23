"""Configure and verify the Spotify Web API connection.

Credentials are stored in a `.env` file at the repo root (see
`.env.sample` for the format) and loaded from there by every notebook
via `dotenv.load_dotenv()`.

Local machine with a browser:
    .venv/bin/python config_helper.py

Headless / remote server (no local browser can reach the redirect URI):
    .venv/bin/python config_helper.py --remote
    -> prints a login URL to open in any browser, then prompts you to
       paste back the URL you were redirected to.

Re-verify a saved connection without re-entering credentials:
    .venv/bin/python config_helper.py --check-only [--remote]
"""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from dotenv import load_dotenv, set_key

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
TOKEN_CACHE_PATH = PROJECT_ROOT / "data" / ".spotify_cache"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
REQUIRED_KEYS = (
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
)


def existing_values() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    load_dotenv(ENV_PATH, override=True)
    return {key: os.environ.get(key, "") for key in REQUIRED_KEYS}


def prompt_for_credentials() -> dict[str, str]:
    current = existing_values()
    print("Paste values from your Spotify Developer Dashboard.")
    print("Press enter to keep the current value shown in [brackets].\n")

    client_id_default = current.get("SPOTIPY_CLIENT_ID", "")
    client_id = input(f"Client ID [{client_id_default or 'none'}]: ").strip() or client_id_default

    client_secret = getpass.getpass("Client secret (hidden, enter to keep current): ").strip()
    client_secret = client_secret or current.get("SPOTIPY_CLIENT_SECRET", "")

    redirect_default = current.get("SPOTIPY_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    redirect_uri = input(f"Redirect URI [{redirect_default}]: ").strip() or redirect_default

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
    ENV_PATH.touch(exist_ok=True)
    ENV_PATH.chmod(0o600)
    for key, value in credentials.items():
        set_key(str(ENV_PATH), key, value)
    print(f"Saved credentials to {ENV_PATH}")


def load_credentials() -> dict[str, str]:
    if not ENV_PATH.exists():
        raise SystemExit(
            f"No credentials found at {ENV_PATH}. "
            "Run this script without --check-only first, or copy .env.sample to .env."
        )
    credentials = existing_values()
    missing = [key for key, value in credentials.items() if not value]
    if missing:
        raise SystemExit(f"{ENV_PATH} is missing: {', '.join(missing)}")
    return credentials


def verify_connection(credentials: dict[str, str], remote: bool) -> None:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    os.environ.update(credentials)
    TOKEN_CACHE_PATH.parent.mkdir(exist_ok=True)
    auth_manager = SpotifyOAuth(
        scope="user-read-recently-played",
        cache_path=str(TOKEN_CACHE_PATH),
        open_browser=not remote,
    )

    has_cached_token = auth_manager.validate_token(auth_manager.cache_handler.get_cached_token()) is not None
    if remote and not has_cached_token:
        print("\nOpen this URL in any browser (it does not need to run on this machine):\n")
        print(auth_manager.get_authorize_url())
        response = input("\nPaste the full URL you were redirected to: ").strip()
        code = auth_manager.parse_response_code(response)
        auth_manager.get_access_token(code, as_dict=False)

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
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Headless flow: print the login URL instead of opening a browser, "
        "and prompt for the redirected URL to paste back in.",
    )
    args = parser.parse_args()

    if args.check_only:
        credentials = load_credentials()
    else:
        credentials = prompt_for_credentials()
        save_credentials(credentials)
    verify_connection(credentials, remote=args.remote)


if __name__ == "__main__":
    main()
