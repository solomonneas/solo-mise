#!/usr/bin/env bash
# ============================================================================
# Workspace backup via restic -> Google Drive (rclone) + local NAS mount
# Run twice daily via cron. Tunable. Sanitize before commit.
# ============================================================================
#
# Setup once:
#   1. apt install restic rclone
#   2. rclone config   # set up `gdrive` remote
#   3. echo "<strong-password>" > ~/.solo-mise/.restic-password
#      chmod 600 ~/.solo-mise/.restic-password
#   4. (optional) mount your NAS at $NAS_MOUNT
#   5. crontab -e:
#        0 3,15 * * * /path/to/backup-restic.sh
#
set -euo pipefail

# ---- Paths ----
WORKSPACE_ROOT="${WORKSPACE_ROOT:-${HOME}}"
LOG_FILE="${WORKSPACE_ROOT}/.solo-mise/logs/backup-$(date +%Y%m%d).log"
mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# ---- Config (edit for your stack) ----
RESTIC_REPO_GDRIVE="rclone:gdrive:Backup/<your-repo-name>"
RESTIC_PASSWORD_FILE="${HOME}/.solo-mise/.restic-password"
NAS_MOUNT="${NAS_MOUNT:-/mnt/nas/backups}"
RESTIC_REPO_NAS="${NAS_MOUNT}/<your-repo-name>"

# Retention policy. Tune to taste; the defaults below cover ~3 months of
# overlapping daily/weekly/monthly snapshots.
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-3}"

# Paths to back up. Replace with your actual roots.
BACKUP_PATHS=(
  "${HOME}/.solo-mise"
  "${HOME}/repos"
  "${HOME}/bin"
  "${HOME}/.bashrc"
  "${HOME}/.profile"
  "${HOME}/.gitconfig"
  "${HOME}/.ssh"
  "${HOME}/.claude"
  "${HOME}/.codex"
  "${HOME}/notes"
  "${HOME}/Obsidian"
)

# Exclude rules shared between gdrive + NAS runs.
EXCLUDES=(
  --exclude='node_modules'
  --exclude='.git/objects'
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='.venv'
  --exclude='venv'
  --exclude='dist'
  --exclude='build'
  --exclude='.next'
  --exclude='.astro'
  --exclude='coverage'
  --exclude='.turbo'
  --exclude='*.jsonl'
  --exclude='.pm2/logs'
  --exclude='.pm2/pids'
  --exclude='.ollama'
  --exclude='.obsidian/workspace*.json'
  --exclude='.obsidian/cache'
  --exclude='.trash'
)

export RESTIC_PASSWORD_FILE

# ---- rclone tuning ----
# Google Drive can reject bursty restic-over-rclone writes when other rclone
# jobs are running. Keep the backend intentionally conservative so scheduled
# backups finish reliably instead of spinning in quota retry loops.
export RCLONE_TRANSFERS="${RCLONE_TRANSFERS:-1}"
export RCLONE_CHECKERS="${RCLONE_CHECKERS:-2}"
export RCLONE_TPSLIMIT="${RCLONE_TPSLIMIT:-4}"
export RCLONE_TPSLIMIT_BURST="${RCLONE_TPSLIMIT_BURST:-4}"
export RCLONE_DRIVE_PACER_MIN_SLEEP="${RCLONE_DRIVE_PACER_MIN_SLEEP:-500ms}"
export RCLONE_DRIVE_PACER_BURST="${RCLONE_DRIVE_PACER_BURST:-10}"
export RCLONE_RETRIES="${RCLONE_RETRIES:-8}"
export RCLONE_LOW_LEVEL_RETRIES="${RCLONE_LOW_LEVEL_RETRIES:-20}"

# ---- Pre-flight ----
command -v restic >/dev/null || { log "ERROR: restic not installed."; exit 1; }
command -v rclone >/dev/null || { log "ERROR: rclone not installed."; exit 1; }
[ -f "$RESTIC_PASSWORD_FILE" ] || { log "ERROR: password file not found: $RESTIC_PASSWORD_FILE"; exit 1; }

# ---- Helper: ensure a restic repo is usable ----
ensure_repo() {
  local repo="$1"
  export RESTIC_REPOSITORY="$repo"
  if restic snapshots --json >/dev/null 2>&1; then
    log "Repo exists at $repo, proceeding."
    return 0
  fi
  log "Initializing repo: $repo"
  if restic init 2>&1 | tee -a "$LOG_FILE"; then
    return 0
  fi
  # init may fail because the repo already exists; retry the check.
  if restic snapshots --json >/dev/null 2>&1; then
    log "Repo already exists (init not needed)."
    return 0
  fi
  log "ERROR: cannot access or initialize repo: $repo"
  return 1
}

# ---- Helper: backup + forget for a configured repo ----
run_backup() {
  local repo="$1"
  local tag="$2"
  export RESTIC_REPOSITORY="$repo"
  log "Backing up to $repo (tag=$tag)..."
  restic backup --verbose --tag "$tag" "${EXCLUDES[@]}" "${BACKUP_PATHS[@]}" 2>&1 | tee -a "$LOG_FILE"
  log "Pruning old snapshots..."
  restic forget \
    --keep-daily "$KEEP_DAILY" \
    --keep-weekly "$KEEP_WEEKLY" \
    --keep-monthly "$KEEP_MONTHLY" \
    --prune 2>&1 | tee -a "$LOG_FILE"
}

# ---- Google Drive backup ----
if ensure_repo "$RESTIC_REPO_GDRIVE"; then
  run_backup "$RESTIC_REPO_GDRIVE" "scheduled"
  log "Clearing any stale gdrive locks..."
  restic unlock --remove-all 2>&1 | tee -a "$LOG_FILE" || true
else
  log "Skipping gdrive backup; repo unavailable."
fi

# ---- NAS backup (skip cleanly if not mounted) ----
if mountpoint -q "${NAS_MOUNT}" 2>/dev/null || [ -d "${NAS_MOUNT}" ]; then
  if ensure_repo "$RESTIC_REPO_NAS"; then
    run_backup "$RESTIC_REPO_NAS" "scheduled-nas"
    restic unlock --remove-all 2>&1 | tee -a "$LOG_FILE" || true
  else
    log "Skipping NAS backup; repo unavailable."
  fi
else
  log "NAS not mounted at ${NAS_MOUNT}, skipping NAS backup."
fi

# ---- Summary ----
export RESTIC_REPOSITORY="$RESTIC_REPO_GDRIVE"
SNAPSHOT_COUNT=$(restic snapshots --json 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
log "Done. Total snapshots (gdrive): $SNAPSHOT_COUNT"
log "Log: $LOG_FILE"
