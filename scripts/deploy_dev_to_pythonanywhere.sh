#!/usr/bin/env bash
set -Eeuo pipefail

# Step-labeled failure reporting: every major phase calls step() to say what
# it's about to do, so a failure anywhere prints "Deploy failed at: <step>"
# with the actual failing command and its captured output, not just a bare
# non-zero exit after the fact. Found 15 Jul 2026 (commit e37c47c): a
# transient SSH connection drop during the status-JSON step aborted the
# whole deploy with zero detail beyond "exited non-zero" - even though the
# actual code sync (scp) had already completed successfully minutes
# earlier. auto_deploy_gate.sh (the caller) captures this script's output
# and forwards the specific failed-step line into the ops-alert email.
CURRENT_STEP="startup"
step() {
  CURRENT_STEP="$1"
  echo ">>> $CURRENT_STEP"
}
on_error() {
  local exit_code=$?
  echo "DEPLOY FAILED AT STEP: $CURRENT_STEP (line $1, command: $2, exit $exit_code)" >&2
  exit "$exit_code"
}
trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

SOURCE_REPO="${POPDAY_SOURCE_REPO:-/Users/jasondunne/Documents/PopDay}"
RUNTIME_DIR="${POPDAY_RUNTIME_DIR:-/Users/jasondunne/PopDayRuntime}"
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
    step "reload confirmation (attempt $attempt/$POPDAY_DEPLOY_VERIFY_RETRIES)"
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
    step "live verification (attempt $attempt/$POPDAY_DEPLOY_VERIFY_RETRIES)"
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

# Since the 14 Jul 2026 scan cutover, PythonAnywhere holds the LIVE database
# (its own scheduled tasks scan directly into it) - see OPERATING_MODEL.md
# Device Roles. This deploy script must never push the Mac Mini's local
# runtime DB up to PA (it would silently destroy live scan data); it only
# pulls a read-only copy of PA's DB down for the Mac Mini's own backup
# retention. A missing/stale local runtime DB no longer blocks shipping a
# code fix.
step "pre-deploy backup (local)"
backup_line="$("$PYTHON_BIN" scripts/backup_popday_runtime.py --reason "pre PythonAnywhere development deploy" --source-repo "$SOURCE_REPO")"
echo "$backup_line"
backup_dir="$(echo "$backup_line" | sed -n 's/^PopDay backup created: //p')"

step "py_compile (local)"
"$PYTHON_BIN" -m py_compile popday.py popday/*.py

step "sync runtime code (local)"
POPDAY_RUNTIME_DIR="$RUNTIME_DIR" PYTHON_BIN="$PYTHON_BIN" bash scripts/sync_runtime_code_from_repo.sh

step "prepare PA directories"
deploy_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
ssh "$PYTHONANYWHERE_SSH_TARGET" "mkdir -p '$PYTHONANYWHERE_APP_DIR/templates' '$PYTHONANYWHERE_APP_DIR/scripts' '$PYTHONANYWHERE_APP_DIR/status' '$PYTHONANYWHERE_APP_DIR/backups' '$PYTHONANYWHERE_APP_DIR/popday' '$PYTHONANYWHERE_APP_DIR/popday/sources'"

step "backup PA database"
ssh "$PYTHONANYWHERE_SSH_TARGET" "if [ -f '$PYTHONANYWHERE_DB_PATH' ]; then cp '$PYTHONANYWHERE_DB_PATH' '$PYTHONANYWHERE_APP_DIR/backups/popday.sqlite3.$deploy_stamp.bak'; fi"

step "pull PA DB copy for Mac Mini backup (non-fatal)"
if [[ -n "$backup_dir" && -d "$backup_dir" ]]; then
  scp "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_DB_PATH" "$backup_dir/popday_pythonanywhere_live.sqlite3" \
    || echo "WARNING: could not pull PythonAnywhere's live DB into the Mac Mini backup; continuing." >&2
fi

step "sync code to PA (scp)"
scp flask_app.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/flask_app.py"
scp popday/*.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/popday/"
scp popday/sources/*.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/popday/sources/"
scp templates/*.html "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/templates/"
scp scripts/backup_popday_runtime.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/backup_popday_runtime.py"
scp scripts/generate_status_json.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/generate_status_json.py"
scp scripts/refresh_price_reaction.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/refresh_price_reaction.py"
scp scripts/verify_live_popday.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/verify_live_popday.py"
scp scripts/verify_live_popday_buttons.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/verify_live_popday_buttons.py"
scp scripts/check_scan_heartbeat.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/check_scan_heartbeat.py"
scp scripts/capture_uk_prices.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/capture_uk_prices.py"
scp scripts/migrate_uk_market.py "$PYTHONANYWHERE_SSH_TARGET:$PYTHONANYWHERE_APP_DIR/scripts/migrate_uk_market.py"

step "clean up stale templates on PA"
ssh "$PYTHONANYWHERE_SSH_TARGET" "rm -f '$PYTHONANYWHERE_APP_DIR/templates/status.html' '$PYTHONANYWHERE_APP_DIR/templates/index.html'"

step "py_compile (PA)"
ssh "$PYTHONANYWHERE_SSH_TARGET" "cd '$PYTHONANYWHERE_APP_DIR' && python3 -m py_compile flask_app.py popday/*.py popday/sources/*.py scripts/generate_status_json.py scripts/refresh_price_reaction.py scripts/capture_uk_prices.py"

# Belt-and-braces: never ship a stale Price Reaction cache or status JSON
# (both are also refreshed automatically after every scan). These now run ON
# PythonAnywhere against its own live DB, not the Mac Mini's copy. Both are
# non-fatal, deliberately and identically — a price-feed blip or a transient
# SSH drop here (e.g. commit e37c47c, 15 Jul 2026: "Connection closed" mid-
# call) should never block deploying code that already synced successfully,
# nor skip the live-verification step that would actually catch a real
# problem.
step "refresh Price Reaction cache (PA, non-fatal)"
ssh "$PYTHONANYWHERE_SSH_TARGET" "cd '$PYTHONANYWHERE_APP_DIR' && python3 scripts/refresh_price_reaction.py --db-path '$PYTHONANYWHERE_DB_PATH'" \
  || echo "WARNING: PythonAnywhere Price Reaction refresh failed; deploying existing cache." >&2

step "generate status JSON (PA, non-fatal)"
ssh "$PYTHONANYWHERE_SSH_TARGET" "cd '$PYTHONANYWHERE_APP_DIR' && python3 scripts/generate_status_json.py --output '$PYTHONANYWHERE_APP_DIR/status/popday_status.json' --source-repo '$PYTHONANYWHERE_APP_DIR' --db-path '$PYTHONANYWHERE_DB_PATH' --runtime-dir '$PYTHONANYWHERE_APP_DIR' --backup-root '$PYTHONANYWHERE_APP_DIR/backups'" \
  || echo "WARNING: PythonAnywhere status JSON generation failed; deploying existing status.json." >&2

step "reload + live verification"
run_live_verifiers_after_reload

echo "PopDay development front door deployed."
echo "Open: https://jasdun.pythonanywhere.com/"
