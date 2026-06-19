#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${POPDAY_SOURCE_REPO:-/Users/jasondunne/Documents/PopDay}"
RUNTIME_DB_PATH="${POPDAY_RUNTIME_DB_PATH:-/Users/jasondunne/PopDayRuntime/popday.sqlite3}"
STATUS_PATH="${POPDAY_STATUS_PATH:-/Users/jasondunne/PopDayRuntime/status/popday_status.json}"
PYTHONANYWHERE_SSH_TARGET="${PYTHONANYWHERE_SSH_TARGET:-Jasdun@ssh.pythonanywhere.com}"
PYTHONANYWHERE_APP_DIR="${PYTHONANYWHERE_APP_DIR:-/home/Jasdun/popday}"
PYTHONANYWHERE_WSGI_PATH="${PYTHONANYWHERE_WSGI_PATH:-/var/www/jasdun_pythonanywhere_com_wsgi.py}"
PYTHONANYWHERE_DB_PATH="${PYTHONANYWHERE_DB_PATH:-$PYTHONANYWHERE_APP_DIR/popday.sqlite3}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

cd "$SOURCE_REPO"

if [[ ! -f "$RUNTIME_DB_PATH" ]]; then
  echo "Missing Mac Mini PopDay runtime database: $RUNTIME_DB_PATH" >&2
  exit 1
fi

"$PYTHON_BIN" scripts/backup_popday_runtime.py --reason "pre PythonAnywhere development deploy"
"$PYTHON_BIN" scripts/generate_status_json.py \
  --output "$STATUS_PATH" \
  --source-repo "$SOURCE_REPO" \
  --db-path "$RUNTIME_DB_PATH"

deploy_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
ssh "$PYTHONANYWHERE_SSH_TARGET" "mkdir -p '$PYTHONANYWHERE_APP_DIR/templates' '$PYTHONANYWHERE_APP_DIR/scripts' '$PYTHONANYWHERE_APP_DIR/status' '$PYTHONANYWHERE_APP_DIR/backups' '$PYTHONANYWHERE_APP_DIR/popday'"
ssh "$PYTHONANYWHERE_SSH_TARGET" "if [ -f '$PYTHONANYWHERE_DB_PATH' ]; then cp '$PYTHONANYWHERE_DB_PATH' '$PYTHONANYWHERE_APP_DIR/backups/popday.sqlite3.$deploy_stamp.bak'; fi"

scp flask_app.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/flask_app.py"
scp popday/*.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/popday/"
scp templates/*.html "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/templates/"
scp scripts/backup_popday_runtime.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/backup_popday_runtime.py"
scp scripts/generate_status_json.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/generate_status_json.py"
scp scripts/sync_status_to_pythonanywhere.sh "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/sync_status_to_pythonanywhere.sh"
scp scripts/verify_live_popday.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/verify_live_popday.py"
scp "$RUNTIME_DB_PATH" "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_DB_PATH.tmp"
ssh "$PYTHONANYWHERE_SSH_TARGET" "mv '$PYTHONANYWHERE_DB_PATH.tmp' '$PYTHONANYWHERE_DB_PATH'"
scp "$STATUS_PATH" "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/status/popday_status.json"

ssh "$PYTHONANYWHERE_SSH_TARGET" "rm -f '$PYTHONANYWHERE_APP_DIR/templates/status.html'"
ssh "$PYTHONANYWHERE_SSH_TARGET" "cd '$PYTHONANYWHERE_APP_DIR' && python3 -m py_compile flask_app.py popday/*.py scripts/generate_status_json.py"
ssh "$PYTHONANYWHERE_SSH_TARGET" "touch '$PYTHONANYWHERE_WSGI_PATH'"

sleep 3
"$PYTHON_BIN" scripts/verify_live_popday.py

echo "PopDay development front door deployed."
echo "Open: https://jasdun.pythonanywhere.com/"
