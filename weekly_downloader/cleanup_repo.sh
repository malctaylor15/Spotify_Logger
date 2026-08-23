#!/usr/bin/env bash
#
# Clean the Spotify_Logger repo and the Python environments left over from the
# 3.13 upgrade, then prepare a tidy commit.
#
# What it does:
#   1. Deletes venv backups, upgrade backups, __pycache__, .DS_Store, empty Slope/
#   2. Strips embedded outputs from tracked notebooks (~10MB -> ~0.5MB)
#   3. Untracks listening_history.db (stays on disk, leaves git)
#   4. Reports what git would then commit
#
# NOTHING is deleted or changed unless you pass --apply. The default is a
# read-only report.
#
# Usage:
#   bash weekly_downloader/cleanup_repo.sh           # report only
#   bash weekly_downloader/cleanup_repo.sh --apply   # actually clean

set -uo pipefail

REPO="/Users/malcolmtaylor/python_related/Spotify_Logger"
PYROOT="$(dirname "$REPO")"
cd "$REPO" || { echo "ERROR: repo not found at $REPO" >&2; exit 1; }

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
run()  { if [[ $APPLY -eq 1 ]]; then eval "$@"; else echo "    [dry-run] $*"; fi; }

human() { du -sh "$1" 2>/dev/null | cut -f1; }

# ------------------------------------------------------- 1. disk leftovers ---
say "Environment and junk cleanup"

TOTAL_FREED=0
shopt -s nullglob
for d in "$PYROOT"/main_env.bak-* "$PYROOT"/*.bak-* "$REPO"/.venv.bak-* \
         "$REPO"/weekly_downloader/python_upgrade_backup_*; do
    [[ -e "$d" ]] || continue
    note "backup: $d ($(human "$d"))"
    run "rm -rf '$d'"
done
shopt -u nullglob

if [[ -d "$REPO/Slope" ]] && [[ -z "$(ls -A "$REPO/Slope" 2>/dev/null)" ]]; then
    note "empty dir: Slope/"
    run "rmdir '$REPO/Slope'"
fi

PYC_COUNT=$(find "$REPO" -name '__pycache__' -type d -not -path '*/.venv/*' 2>/dev/null | wc -l | tr -d ' ')
DS_COUNT=$(find "$REPO" -name '.DS_Store' -not -path '*/.venv/*' 2>/dev/null | wc -l | tr -d ' ')
note "__pycache__ dirs: $PYC_COUNT    .DS_Store files: $DS_COUNT"
run "find '$REPO' -name '__pycache__' -type d -not -path '*/.venv/*' -exec rm -rf {} + 2>/dev/null"
run "find '$REPO' -name '.DS_Store' -not -path '*/.venv/*' -delete 2>/dev/null"

# ---------------------------------------- 2. strip notebook outputs in git ---
say "Notebook outputs"

# Strip only what git tracks; untracked scratch notebooks are left alone.
NOTEBOOKS="$(git ls-files '*.ipynb' 2>/dev/null)"
if [[ -z "$NOTEBOOKS" ]]; then
    note "no tracked notebooks found"
else
    while IFS= read -r nb; do
        [[ -f "$nb" ]] || continue
        SIZE=$(wc -c < "$nb" | tr -d ' ')
        printf '    %-46s %8s bytes\n' "$nb" "$SIZE"
    done <<< "$NOTEBOOKS"

    if [[ $APPLY -eq 1 ]]; then
        python3 - "$REPO" <<'PYEOF'
import json, subprocess, sys
from pathlib import Path

repo = Path(sys.argv[1])
tracked = subprocess.run(["git", "ls-files", "*.ipynb"], cwd=repo,
                         capture_output=True, text=True).stdout.split("\n")
total_before = total_after = 0
for rel in filter(None, tracked):
    path = repo / rel
    if not path.exists():
        continue
    before = path.stat().st_size
    try:
        nb = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"    SKIP {rel}: {exc}")
        continue
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
    # Drop volatile kernel metadata that churns diffs for no benefit.
    nb.get("metadata", {}).pop("widgets", None)
    path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
    after = path.stat().st_size
    total_before += before
    total_after += after
    print(f"    stripped {rel}: {before:,} -> {after:,} bytes")
if total_before:
    saved = (1 - total_after / total_before) * 100
    print(f"    TOTAL: {total_before:,} -> {total_after:,} bytes ({saved:.0f}% smaller)")
PYEOF
    else
        note "[dry-run] would clear outputs + execution counts from the above"
    fi
fi

# ------------------------------------------------ 3. untrack the sample DB ---
say "Sample database"
if git ls-files --error-unmatch listening_history.db >/dev/null 2>&1; then
    note "listening_history.db is tracked ($(human listening_history.db)) - untracking"
    note "(the file stays on disk; real data lives in the gitignored data/)"
    run "git rm --cached -q listening_history.db"
else
    note "listening_history.db is not tracked - nothing to do"
fi

# Make sure it can't come back.
if ! grep -qx 'listening_history.db' .gitignore 2>/dev/null; then
    if [[ $APPLY -eq 1 ]]; then
        printf '\n# Sample database (real data lives in data/)\nlistening_history.db\n' >> .gitignore
        note "added listening_history.db to .gitignore"
    else
        note "[dry-run] would add listening_history.db to .gitignore"
    fi
fi

# --------------------------------------------------------- 4. what remains ---
say "Repo status after cleanup"
git status --short | head -40

say "Largest tracked files"
git ls-files | while IFS= read -r f; do
    [[ -f "$f" ]] && printf '%10s  %s\n' "$(wc -c < "$f" | tr -d ' ')" "$f"
done | sort -rn | head -12

if [[ $APPLY -eq 0 ]]; then
    say "Dry run - nothing changed"
    echo "    Re-run with --apply to perform the cleanup."
else
    say "Cleanup complete"
    echo "    Next:  bash weekly_downloader/commit_weekly_downloader.sh"
    echo "    Then:  bash weekly_downloader/commit_weekly_downloader.sh --push"
fi
