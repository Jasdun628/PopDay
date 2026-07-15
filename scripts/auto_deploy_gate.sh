#!/usr/bin/env bash
# Runs 1-2x/day via launchd. Only ever ships code that is: committed,
# pushed to GitHub, and passing the full test suite. Silent when there is
# nothing new to ship. Alarms (email, via the same ops_alerts machinery as
# scan failures) if tests fail or the deploy itself fails - never leaves a
# broken or half-deployed state unreported.
set -uo pipefail

SOURCE_REPO="${POPDAY_SOURCE_REPO:-/Users/jasondunne/Documents/PopDay}"
CONFIG_PATH="${POPDAY_CONFIG_PATH:-$SOURCE_REPO/config.json}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
MARKER_FILE="$SOURCE_REPO/.last_deployed_commit"

cd "$SOURCE_REPO" || exit 1

# This repo's remote is named "github" (not "origin"); detect it rather than
# hardcode either name.
GIT_REMOTE="${POPDAY_GIT_REMOTE:-$(git remote | head -n1)}"

notify() {
  # $1 = --broken or --recovered, $2 = detail text
  "$PYTHON_BIN" scripts/notify_deploy_failure.py --config "$CONFIG_PATH" "$1" --detail "$2"
}

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Uncommitted changes present - skipping automated deploy for now (not an error, just waiting for a clean commit)."
  exit 0
fi

# Sync with GitHub first. macOS TCC blocks launchd jobs from ~/Documents, so
# the scheduled job runs from a dedicated clone outside Documents and GitHub
# is the hand-off point: only committed AND pushed code ever deploys, and PA
# can never silently get ahead of GitHub. Fast-forward only - this checkout
# is never hand-edited, and a non-ff state means something needs a human.
current_branch="$(git rev-parse --abbrev-ref HEAD)"
if ! git fetch "$GIT_REMOTE"; then
  notify --broken "Automated deploy skipped: 'git fetch $GIT_REMOTE' failed. Nothing was deployed. Check GitHub connectivity/credentials on the Mac Mini."
  exit 1
fi
if ! git merge --ff-only "$GIT_REMOTE/$current_branch"; then
  notify --broken "Automated deploy skipped: local branch $current_branch has diverged from $GIT_REMOTE/$current_branch and cannot fast-forward. Reconcile manually; nothing was deployed."
  exit 1
fi

current_head="$(git rev-parse HEAD)"
last_deployed="$(cat "$MARKER_FILE" 2>/dev/null || echo "")"

if [[ "$current_head" == "$last_deployed" ]]; then
  echo "Nothing new since the last deploy ($current_head) - skipping."
  exit 0
fi

echo "New commit to ship: $current_head (last deployed: ${last_deployed:-none}). Running tests..."
if ! "$PYTHON_BIN" -m pytest tests/ -q; then
  notify --broken "Automated deploy skipped: the test suite is failing on commit $current_head. Nothing was pushed or deployed. Fix the failing test(s) and the next scheduled run will try again."
  exit 1
fi

echo "Tests passed. Pushing to GitHub..."
if ! git push "$GIT_REMOTE" "$current_branch"; then
  notify --broken "Automated deploy skipped: 'git push $GIT_REMOTE $current_branch' failed for commit $current_head. Nothing was deployed. Check GitHub connectivity/credentials on the Mac Mini."
  exit 1
fi

echo "Push succeeded. Running the PythonAnywhere deploy script..."
# Capture the deploy script's output (still streamed live to this script's
# own stdout/stderr via tee, so the launchd log keeps the full transcript)
# so a failure can be reported by its specific step, not just a bare exit
# code. deploy_dev_to_pythonanywhere.sh prints "DEPLOY FAILED AT STEP: ..."
# right before exiting on any step failure (added 15 Jul 2026, after commit
# e37c47c's deploy failed with no detail beyond "exited non-zero").
deploy_log="$(mktemp)"
bash scripts/deploy_dev_to_pythonanywhere.sh 2>&1 | tee "$deploy_log"
deploy_exit="${PIPESTATUS[0]}"
if [[ "$deploy_exit" -ne 0 ]]; then
  failed_step="$(grep -m1 '^DEPLOY FAILED AT STEP:' "$deploy_log" || true)"
  if [[ -n "$failed_step" ]]; then
    detail="Automated deploy failed for commit $current_head. GitHub has the new code, but PythonAnywhere may still be running the previous version - check manually. $failed_step"
  else
    detail="Automated deploy failed: deploy_dev_to_pythonanywhere.sh exited non-zero (exit $deploy_exit) for commit $current_head, but no step marker was captured - check the deploy log directly. GitHub has the new code, but PythonAnywhere may still be running the previous version."
  fi
  notify --broken "$detail"
  rm -f "$deploy_log"
  exit 1
fi
rm -f "$deploy_log"

echo "$current_head" > "$MARKER_FILE"
notify --recovered ""
echo "Deploy complete and verified. Marker updated to $current_head."
