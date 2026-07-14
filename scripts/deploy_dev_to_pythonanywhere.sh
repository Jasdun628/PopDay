#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${POPDAY_SOURCE_REPO:-/Users/jasondunne/Documents/PopDay}"
RUNTIME_DB_PATH="${POPDAY_RUNTIME_DB_PATH:-/Users/jasondunne/PopDayRuntime/popday.sqlite3}"
RUNTIME_DIR="${POPDAY_RUNTIME_DIR:-/Users/jasondunne/PopDayRuntime}"
STATUS_PATH="${POPDAY_STATUS_PATH:-/Users/jasondunne/PopDayRuntime/status/popday_status.json}"
PYTHONANYWHERE_SSH_TARGET="${PYTHONANYWHERE_SSH_TARGET:-Jasdun@ssh.pythonanywhere.com}"
PYTHONANYWHERE_APP_DIR="${PYTHONANYWHERE_APP_DIR:-/home/Jasdun/popday}"
PYTHONANYWHERE_WSGI_PATH="${PYTHONANYWHERE_WSGI_PATH:-/var/www/jasdun_pythonanywhere_com_wsgi.py}"
PYTHONANYWHERE_DB_PATH="${PYTHONANYWHERE_DB_PATH:-$PYTHONANYWHERE_APP_DIR/popday.sqlite3}"
PYTHONANYWHERE_SERVER_LOG="${PYTHONANYWHERE_SERVER_LOG:-/var/log/jasdun.pythonanywhere.com.server.log}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
POPDAY_DEPLOY_RELOAD_WAIT_SECONDS="${POPDAY_DEPLOY_RELOAD_WAIT_SECONDS:-10}"
POPDAY_DEPLOY_RELOAD_CONFIRM_TIMEOUT_SECONDS="${POPDAY_DEPLOY_RELOAD_CONFIRM_TIMEOUT_SECONDS:-70}"
POPDAY_DEPLOY_VERIFY_RETRIES="${POPDAY_DEPLOY_VERIFY_RETRIES:-3}"

touch_pythonanywhere_wsgi() {
  ssh "$PYTHONANYWHERE_SSH_TARGET" "touch '$PYTHONANYWHERE_WSGI_PATH'"
}

# uWSGI's own log logs "your mercy for graceful operations on workers is 60
# seconds" — a touch fired within that window of the previous reload can be
# silently ignored, so the app keeps serving a stale in-memory Jinja template
# cache even though the file on disk (and py_compile) is current. Confirming a
# NEW worker spawn (rather than just sleeping and hoping) is what catches this;
# discovered 7 Jul 2026 when rapid successive deploys left old copy live while
# "verification passed" (the verifiers check pages load, not exact text).
last_reload_marker() {
  ssh "$PYTHONANYWHERE_SSH_TARGET" \
    "grep 'spawned uWSGI master process' '$PYTHONANYWHERE_SERVER_LOG' 2>/dev/null | tail -1"
}

wait_for_confirmed_reload() {
  local before="$1"
  local waited=0
  local after=""
  while (( waited < POPDAY_DEPLOY_RELOAD_CONFIRM_TIMEOUT_SECONDS )); do
    sleep 5
    waited=$((waited + 5))
    after="$(last_reload_marker)"
    if [[ -n "$after" && "$after" != "$before" ]]; then
      echo "Reload confirmed (new PythonAnywhere worker spawn after ${waited}s)."
      return 0
    fi
  done
  echo "WARNING: no fresh PythonAnywhere worker spawn confirmed within ${POPDAY_DEPLOY_RELOAD_CONFIRM_TIMEOUT_SECONDS}s; the reload may have been ignored." >&2
  return 1
}

run_live_verifiers_after_reload() {
  local attempt
  local before_marker
  attempt=1
  while (( attempt <= POPDAY_DEPLOY_VERIFY_RETRIES )); do
    echo "Requesting PythonAnywhere reload before verification attempt $attempt/$POPDAY_DEPLOY_VERIFY_RETRIES..."
    before_marker="$(last_reload_marker || true)"
    touch_pythonanywhere_wsgi
    sleep "$POPDAY_DEPLOY_RELOAD_WAIT_SECONDS"
    if ! wait_for_confirmed_reload "$before_marker"; then
      # Reload wasn't confirmed — most likely uWSGI's 60s grace period
      # swallowed this touch. Wait clear of it so the next attempt's touch
      # actually lands, rather than immediately re-touching and looping.
      echo "Waiting clear of PythonAnywhere's reload grace period before retrying..." >&2
      sleep 65
    fi
    if "$PYTHON_BIN" scripts/verify_live_popday.py && "$PYTHON_BIN" scripts/verify_live_popday_buttons.py; then
      return 0
    fi
    if (( attempt < POPDAY_DEPLOY_VERIFY_RETRIES )); then
      echo "PopDay live verification failed after reload attempt $attempt; retrying with a fresh reload..." >&2
    fi
    attempt=$((attempt + 1))
  done
  echo "PopDay live verification failed after $POPDAY_DEPLOY_VERIFY_RETRIES reload attempts." >&2
  return 1
}

cd "$SOURCE_REPO"

if [[ ! -f "$RUNTIME_DB_PATH" ]]; then
  echo "Missing Mac Mini PopDay runtime database: $RUNTIME_DB_PATH" >&2
  exit 1
fi

"$PYTHON_BIN" scripts/backup_popday_runtime.py --reason "pre PythonAnywhere development deploy" --source-repo "$SOURCE_REPO"
"$PYTHON_BIN" -m py_compile popday.py popday/*.py
POPDAY_RUNTIME_DIR="$RUNTIME_DIR" PYTHON_BIN="$PYTHON_BIN" bash scripts/sync_runtime_code_from_repo.sh
# Belt-and-braces: never ship a stale Price Reaction cache (it is also
# refreshed automatically after every scan). Non-fatal — a price-feed blip
# should not block deploying a fix.
"$PYTHON_BIN" scripts/refresh_price_reaction.py \
  || echo "WARNING: Price Reaction refresh failed; deploying existing cache." >&2
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
scp scripts/verify_live_popday_buttons.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/verify_live_popday_buttons.py"
scp scripts/check_scan_heartbeat.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/check_scan_heartbeat.py"
scp "$RUNTIME_DB_PATH" "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_DB_PATH.tmp"
ssh "$PYTHONANYWHERE_SSH_TARGET" "mv '$PYTHONANYWHERE_DB_PATH.tmp' '$PYTHONANYWHERE_DB_PATH'"
scp "$STATUS_PATH" "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/status/popday_status.json"

ssh "$PYTHONANYWHERE_SSH_TARGET" "rm -f '$PYTHONANYWHERE_APP_DIR/templates/status.html'"
ssh "$PYTHONANYWHERE_SSH_TARGET" "cd '$PYTHONANYWHERE_APP_DIR' && python3 -m py_compile flask_app.py popday/*.py scripts/generate_status_json.py"

run_live_verifiers_after_reload

echo "PopDay development front door deployed."
echo "Open: https://jasdun.pythonanywhere.com/"
