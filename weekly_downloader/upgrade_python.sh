#!/usr/bin/env bash
#
# Upgrade Homebrew Python to 3.13 and rebuild the virtualenvs on top of it.
#
# Targets:
#   1. Homebrew python@3.13
#   2. /Users/malcolmtaylor/python_related/main_env      (the active venv)
#   3. /Users/malcolmtaylor/python_related/Spotify_Logger/.venv
#
# Safety: existing venvs are RENAMED, never deleted. Package lists are frozen
# to disk first, so you can always roll back or reinstall by hand.
#
# Usage:
#   bash weekly_downloader/upgrade_python.sh            # do the upgrade
#   bash weekly_downloader/upgrade_python.sh --check    # report only, change nothing

set -uo pipefail

PY_SERIES="3.13"
PYTHON_ROOT="/Users/malcolmtaylor/python_related"
MAIN_ENV="${PYTHON_ROOT}/main_env"
REPO="${PYTHON_ROOT}/Spotify_Logger"
REPO_VENV="${REPO}/.venv"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${REPO}/weekly_downloader/python_upgrade_backup_${STAMP}"

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARNING: %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- inspect ---
say "Current state"
for env_path in "$MAIN_ENV" "$REPO_VENV"; do
    if [[ -x "${env_path}/bin/python3" ]]; then
        printf '  %-60s %s\n' "$env_path" "$("${env_path}/bin/python3" -V 2>&1)"
    else
        printf '  %-60s %s\n' "$env_path" "(not found)"
    fi
done
printf '  %-60s %s\n' "$(command -v python3 || echo 'python3 not on PATH')" \
       "$(python3 -V 2>&1 || true)"

if [[ $CHECK_ONLY -eq 1 ]]; then
    say "Check only - nothing changed."
    exit 0
fi

command -v brew >/dev/null || die "Homebrew not found. Install it from https://brew.sh"

# ------------------------------------------------------------- freeze pkgs ---
say "Backing up package lists to ${BACKUP_DIR}"
mkdir -p "$BACKUP_DIR" || die "could not create backup dir"
for env_path in "$MAIN_ENV" "$REPO_VENV"; do
    name="$(basename "$env_path")"
    if [[ -x "${env_path}/bin/python3" ]]; then
        "${env_path}/bin/python3" -m pip freeze > "${BACKUP_DIR}/${name}-requirements.txt" 2>/dev/null \
            && echo "  froze ${name} ($(wc -l < "${BACKUP_DIR}/${name}-requirements.txt" | tr -d ' ') packages)" \
            || warn "could not freeze ${name}"
    fi
done

# ----------------------------------------------------------- install python ---
say "Installing Homebrew python@${PY_SERIES}"
brew install "python@${PY_SERIES}" || die "brew install failed"
brew link --overwrite "python@${PY_SERIES}" 2>/dev/null || true

NEW_PY="$(brew --prefix "python@${PY_SERIES}")/bin/python${PY_SERIES}"
[[ -x "$NEW_PY" ]] || die "expected interpreter not found at ${NEW_PY}"
echo "  using ${NEW_PY} -> $("$NEW_PY" -V)"

# -------------------------------------------------------------- rebuild env ---
rebuild_venv() {
    local env_path="$1" req_file="$2" name
    name="$(basename "$env_path")"

    say "Rebuilding ${name}"
    if [[ -d "$env_path" ]]; then
        mv "$env_path" "${env_path}.bak-${STAMP}" \
            || die "could not move aside ${env_path}"
        echo "  old env preserved at ${env_path}.bak-${STAMP}"
    fi

    "$NEW_PY" -m venv "$env_path" || die "venv creation failed for ${name}"
    "${env_path}/bin/python3" -m pip install --upgrade pip -q || warn "pip upgrade failed"

    if [[ -f "$req_file" ]]; then
        echo "  installing from $(basename "$req_file") ..."
        if "${env_path}/bin/python3" -m pip install -r "$req_file" -q; then
            echo "  packages restored"
        else
            warn "some packages failed for ${name}; see ${req_file}"
        fi
    fi
    echo "  ${name} now $("${env_path}/bin/python3" -V)"
}

# The repo venv is defined by requirements.txt; main_env by its frozen list.
rebuild_venv "$REPO_VENV" "${REPO}/requirements.txt"
rebuild_venv "$MAIN_ENV"  "${BACKUP_DIR}/main_env-requirements.txt"

# ------------------------------------------------- downloader dependencies ---
say "Reinstalling downloader dependencies"
"${MAIN_ENV}/bin/python3" -m pip install -U "yt-dlp[default]" -q \
    && echo "  yt-dlp[default] installed (includes the EJS solver)" \
    || warn "yt-dlp install failed"

# ------------------------------------------------------------------ verify ---
say "Verification"
for env_path in "$MAIN_ENV" "$REPO_VENV"; do
    printf '  %-60s %s\n' "$env_path" "$("${env_path}/bin/python3" -V 2>&1)"
done

if [[ -f "${REPO}/weekly_downloader/download_weekly.py" ]]; then
    echo
    "${MAIN_ENV}/bin/python3" "${REPO}/weekly_downloader/download_weekly.py" --doctor
fi

say "Done"
cat <<EOF
  Backups:      ${BACKUP_DIR}
  Old envs:     ${MAIN_ENV}.bak-${STAMP}
                ${REPO_VENV}.bak-${STAMP}

  Open a NEW terminal (or re-activate main_env) so the new interpreter is
  picked up. Verify with:  python3 -V

  Once everything works, remove the .bak-${STAMP} directories by hand.
EOF
