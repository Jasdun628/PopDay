#!/usr/bin/env bash
set -euo pipefail

cd /home/Jasdun/popday

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "Refusing to sync: branch is $branch, expected main." >&2
  exit 1
fi

if [[ -z "$(git status --porcelain)" ]]; then
  echo "No PythonAnywhere changes to sync."
  exit 0
fi

git add -A
git commit -m "Nightly PythonAnywhere sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push origin main
