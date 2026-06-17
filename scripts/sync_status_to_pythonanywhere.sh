#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${POPDAY_SOURCE_REPO:-/Users/jasondunne/Documents/PopDay}"
RUNTIME_DB_PATH="${POPDAY_RUNTIME_DB_PATH:-/Users/jasondunne/PopDayRuntime/popday.sqlite3}"
STATUS_PATH="${POPDAY_STATUS_PATH:-/Users/jasondunne/PopDayRuntime/status/popday_status.json}"
PYTHONANYWHERE_SSH_TARGET="${PYTHONANYWHERE_SSH_TARGET:-Jasdun@ssh.pythonanywhere.com}"
PYTHONANYWHERE_STATUS_PATH="${PYTHONANYWHERE_STATUS_PATH:-/home/Jasdun/popday/status/popday_status.json}"
PYTHONANYWHERE_DB_PATH="${PYTHONANYWHERE_DB_PATH:-/home/Jasdun/popday/popday.sqlite3}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

cd "$SOURCE_REPO"

if [[ ! -f "$RUNTIME_DB_PATH" ]]; then
  echo "Missing Mac Mini PopDay runtime database: $RUNTIME_DB_PATH" >&2
  exit 1
fi

"$PYTHON_BIN" scripts/generate_status_json.py \
  --output "$STATUS_PATH" \
  --source-repo "$SOURCE_REPO" \
  --db-path "$RUNTIME_DB_PATH"

remote_dir="${PYTHONANYWHERE_STATUS_PATH%/*}"
remote_db_dir="${PYTHONANYWHERE_DB_PATH%/*}"
remote_backup_dir="$remote_db_dir/backups"
sync_stamp="$(date -u +%Y%m%dT%H%M%SZ)"

ssh "$PYTHONANYWHERE_SSH_TARGET" "mkdir -p '$remote_dir' '$remote_db_dir' '$remote_backup_dir'"
ssh "$PYTHONANYWHERE_SSH_TARGET" "if [ -f '$PYTHONANYWHERE_DB_PATH' ]; then cp '$PYTHONANYWHERE_DB_PATH' '$remote_backup_dir/popday.sqlite3.$sync_stamp.bak'; fi"
scp "$RUNTIME_DB_PATH" "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_DB_PATH.tmp"
ssh "$PYTHONANYWHERE_SSH_TARGET" "mv '$PYTHONANYWHERE_DB_PATH.tmp' '$PYTHONANYWHERE_DB_PATH'"
scp "$STATUS_PATH" "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_STATUS_PATH"

echo "PopDay database synced to $PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_DB_PATH"
echo "PopDay status synced to $PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_STATUS_PATH"
