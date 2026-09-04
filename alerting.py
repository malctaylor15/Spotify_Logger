#!/usr/bin/env python3
"""Failure alerting shared by every scheduled job in this repo.

Before this existed, only ingestion alerted on failure (via a bespoke SMTP
block inside Run_Notebooks.ipynb). The weekly report, the monthly report and
the stem downloader all exited non-zero into a cron log that nobody reads, so
a broken report was invisible until someone noticed the email had stopped
arriving.

Every job now routes failures through here, so an alert names the job, its
exit status, when it failed, and the tail of its log.

From Python:

    import alerting
    alerting.send_failure_alert("Weekly report", exit_status=1,
                                log_path="run_logs/weekly.log")

From a shell script:

    .venv/bin/python -m alerting --job "Weekly report" \
        --status "$status" --log "$LOG"

Credentials: `data/email_pw.pkl`, a pickled dict with a "pw" key (a Gmail app
password). Addresses default to the ones the other jobs already use and can be
overridden with ALERT_FROM / ALERT_TO / ALERT_GMAIL_LOGIN.

Sending is best-effort by design: this is the error path, and an alert that
cannot be delivered must never mask the original failure. Every problem is
reported on stderr and the function returns False rather than raising.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pickle
import smtplib
import socket
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

GMAIL_LOGIN = os.environ.get("ALERT_GMAIL_LOGIN", "malctaylordev@gmail.com")
SENDER_EMAIL = os.environ.get("ALERT_FROM", "malctaylordev@gmail.com")
RECEIVER_EMAIL = os.environ.get("ALERT_TO", "malctaylor15@gmail.com")
SMTP_PORT = 465

PASSWORD_PATHS = [
    REPO_ROOT / "data" / "email_pw.pkl",
    REPO_ROOT.parent / "credentials" / "email_pw.pkl",
]

LOG_TAIL_LINES = 60


def load_email_password() -> str | None:
    for path in PASSWORD_PATHS:
        if not path.exists():
            continue
        try:
            with open(path, "rb") as fh:
                return pickle.load(fh)["pw"]
        except (pickle.UnpicklingError, KeyError, EOFError, OSError) as exc:
            print(f"WARNING: could not read {path}: {exc}", file=sys.stderr)
    return None


def tail(path: str | Path, lines: int = LOG_TAIL_LINES) -> str:
    """Last `lines` lines of a log file, or a note explaining why not."""
    p = Path(path)
    if not p.exists():
        return f"(no log file at {p})"
    try:
        content = p.read_text(errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read {p}: {exc})"
    if not content:
        return f"(log file {p} is empty)"
    clipped = content[-lines:]
    prefix = (f"... showing last {lines} of {len(content)} lines ...\n"
              if len(content) > lines else "")
    return prefix + "\n".join(clipped)


def build_bodies(job: str, exit_status: int | None, message: str | None,
                 log_excerpt: str | None) -> tuple[str, str]:
    when = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    host = socket.gethostname()

    parts = [
        f"Job:      {job}",
        f"Host:     {host}",
        f"Failed:   {when}",
    ]
    if exit_status is not None:
        parts.append(f"Exit:     {exit_status}")
    text = "\n".join(parts)
    if message:
        text += f"\n\n{message}"
    if log_excerpt:
        text += f"\n\n--- log ---\n{log_excerpt}"

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    rows = "".join(
        f"<tr><td style='padding:2px 12px 2px 0;color:#666'>{k}</td>"
        f"<td style='padding:2px 0'><b>{esc(str(v))}</b></td></tr>"
        for k, v in [("Job", job), ("Host", host), ("Failed", when)]
        + ([("Exit status", exit_status)] if exit_status is not None else [])
    )
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;
        max-width:760px;color:#111">
      <h2 style="margin-bottom:4px;color:#b00020">Scheduled job failed</h2>
      <table style="border-collapse:collapse;margin:8px 0 16px 0">{rows}</table>
      {f"<p>{esc(message)}</p>" if message else ""}
      {f"<h3 style='margin-bottom:4px'>Log</h3>"
        f"<pre style='background:#f6f6f6;padding:12px;overflow-x:auto;"
        f"font-size:12px;line-height:1.4'>{esc(log_excerpt)}</pre>"
        if log_excerpt else ""}
    </div>
    """
    return text, html


def send_failure_alert(job: str, exit_status: int | None = None,
                       message: str | None = None,
                       log_path: str | Path | None = None,
                       log_tail_lines: int = LOG_TAIL_LINES) -> bool:
    """Email a failure alert. Returns True if it was sent.

    Never raises: this runs on the error path, where an alerting problem must
    not replace or hide the failure being reported.
    """
    try:
        log_excerpt = tail(log_path, log_tail_lines) if log_path else None
        subject = f"[Spotify Logger] {job} FAILED"
        if exit_status is not None:
            subject += f" (exit {exit_status})"
        text_body, html_body = build_bodies(job, exit_status, message, log_excerpt)

        # Set ALERT_DRY_RUN=1 to exercise a job's failure path without mailing.
        if os.environ.get("ALERT_DRY_RUN"):
            print(f"[ALERT_DRY_RUN] would send: {subject}\n{text_body}")
            return True

        password = load_email_password()
        if not password:
            tried = "\n  ".join(str(p) for p in PASSWORD_PATHS)
            print(f"ERROR: no email password found, alert not sent. Looked in:\n  {tried}",
                  file=sys.stderr)
            print(text_body, file=sys.stderr)
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", SMTP_PORT, context=context) as server:
            server.login(GMAIL_LOGIN, password)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"Failure alert for '{job}' emailed to {RECEIVER_EMAIL}")
        return True
    except Exception as exc:                                # noqa: BLE001
        print(f"ERROR: could not send failure alert for '{job}': "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Email a failure alert for a scheduled job.")
    parser.add_argument("--job", required=True,
                        help="Human-readable job name, e.g. 'Weekly report'")
    parser.add_argument("--status", type=int, default=None,
                        help="Exit status of the failed job")
    parser.add_argument("--message", default=None,
                        help="Extra context to include in the body")
    parser.add_argument("--log", default=None,
                        help="Path to the job's log file; its tail is attached")
    parser.add_argument("--lines", type=int, default=LOG_TAIL_LINES,
                        help=f"Log lines to include (default {LOG_TAIL_LINES})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the alert instead of sending it")
    args = parser.parse_args()

    if args.dry_run:
        excerpt = tail(args.log, args.lines) if args.log else None
        text, _ = build_bodies(args.job, args.status, args.message, excerpt)
        print(f"Subject: [Spotify Logger] {args.job} FAILED"
              + (f" (exit {args.status})" if args.status is not None else ""))
        print()
        print(text)
        return 0

    ok = send_failure_alert(args.job, exit_status=args.status,
                            message=args.message, log_path=args.log,
                            log_tail_lines=args.lines)
    # Exit 0 even when sending failed: the caller is already failing, and this
    # process's status should not overwrite the real one.
    return 0 if ok else 0


if __name__ == "__main__":
    sys.exit(main())
