#!/usr/bin/env python3
"""Save a Gmail app password for the report emails.

Prompts for the password locally with getpass (hidden while typing) and writes
it to the pickle the mailer reads. The value is never printed, logged, or sent
anywhere.

Gmail rejects normal account passwords over SMTP with:

    534 5.7.9 Application-specific password required

You need a 16-character *App Password*, generated at
https://myaccount.google.com/apppasswords (requires 2-Step Verification).
Your regular Google password will not work, no matter how many times it is
reset.

Usage:
    python3 set_email_password.py            # prompt and save
    python3 set_email_password.py --check    # report where a password is found
    python3 set_email_password.py --test     # verify it by logging in to Gmail
"""

import argparse
import getpass
import pickle
import smtplib
import ssl
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# Same order analyze_stems.py searches; the first is preferred because data/
# is gitignored.
PASSWORD_PATHS = [
    REPO_ROOT / "data" / "email_pw.pkl",
    REPO_ROOT.parent / "credentials" / "email_pw.pkl",
]

GMAIL_LOGIN = "malctaylordev@gmail.com"
SMTP_PORT = 465


def find_existing() -> list[Path]:
    return [p for p in PASSWORD_PATHS if p.exists()]


def load_password() -> tuple[str, Path] | tuple[None, None]:
    for path in PASSWORD_PATHS:
        if path.exists():
            try:
                with path.open("rb") as fh:
                    pw = pickle.load(fh)["pw"]
                if pw:
                    return pw, path
            except (KeyError, EOFError, pickle.UnpicklingError):
                continue
    return None, None


def test_login(password: str) -> bool:
    print(f"Testing SMTP login as {GMAIL_LOGIN} ...")
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", SMTP_PORT, context=context) as s:
            s.login(GMAIL_LOGIN, password)
    except smtplib.SMTPAuthenticationError as exc:
        code = getattr(exc, "smtp_code", "?")
        detail = getattr(exc, "smtp_error", b"").decode(errors="ignore")
        print(f"  LOGIN REJECTED ({code}): {detail}", file=sys.stderr)
        if "Application-specific password required" in detail:
            print("\n  That is an account password, not an App Password.\n"
                  "  Generate a 16-character App Password at:\n"
                  "    https://myaccount.google.com/apppasswords\n"
                  "  (2-Step Verification must be enabled first.)",
                  file=sys.stderr)
        return False
    except (smtplib.SMTPException, OSError) as exc:
        print(f"  Connection problem: {exc}", file=sys.stderr)
        return False
    print("  Login OK.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="Report where a password is stored, without changing it")
    parser.add_argument("--test", action="store_true",
                        help="Test the stored password by logging in to Gmail")
    parser.add_argument("--path", type=Path, default=None,
                        help=f"Where to write (default: {PASSWORD_PATHS[0]})")
    args = parser.parse_args()

    if args.check or args.test:
        found = find_existing()
        if not found:
            print("No password file found. Searched:")
            for p in PASSWORD_PATHS:
                print(f"  {p}")
            return 1
        print("Password file(s) found:")
        for p in found:
            print(f"  {p}")
        pw, used = load_password()
        if not pw:
            print("  ...but no usable 'pw' key inside.", file=sys.stderr)
            return 1
        print(f"Mailer will use: {used}  (length {len(pw)} chars)")
        if len(pw.replace(' ', '')) != 16:
            print("  NOTE: Gmail App Passwords are 16 characters. This one is "
                  f"{len(pw.replace(' ', ''))} - it may be an account password, "
                  "which Gmail rejects.")
        if args.test:
            return 0 if test_login(pw) else 1
        return 0

    dest = args.path or PASSWORD_PATHS[0]
    print(f"Saving a Gmail App Password for {GMAIL_LOGIN}")
    print(f"Destination: {dest}")
    print("Generate one at https://myaccount.google.com/apppasswords "
          "(16 characters).\n")

    pw = getpass.getpass("App Password (hidden): ").strip()
    if not pw:
        print("Nothing entered; aborting.", file=sys.stderr)
        return 1

    compact = pw.replace(" ", "")
    if len(compact) != 16:
        print(f"\nWarning: got {len(compact)} characters; App Passwords are 16.")
        if input("Save anyway? [y/N] ").strip().lower() != "y":
            print("Aborted.")
            return 1
    pw = compact

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        pickle.dump({"pw": pw}, fh)
    dest.chmod(0o600)
    print(f"\nSaved to {dest} (permissions 600).")

    # An existing file earlier in the search order would silently win.
    shadowed = [p for p in PASSWORD_PATHS
                if p.exists() and p != dest
                and PASSWORD_PATHS.index(p) < PASSWORD_PATHS.index(dest)]
    if shadowed:
        print("\nWARNING: these files come first in the search order and will "
              "be used instead:")
        for p in shadowed:
            print(f"  {p}")

    if test_login(pw):
        print("\nReady. Send the report with:")
        print("  python3 weekly_downloader/analyze_stems.py --from-db --send")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
