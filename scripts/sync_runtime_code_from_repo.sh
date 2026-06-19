#!/usr/bin/env bash
set -euo pipefail

SOURCE_REPO="${POPDAY_SOURCE_REPO:-/Users/jasondunne/Documents/PopDay}"
RUNTIME_DIR="${POPDAY_RUNTIME_DIR:-/Users/jasondunne/PopDayRuntime}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

cd "$SOURCE_REPO"

if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "Missing Mac Mini PopDay runtime directory: $RUNTIME_DIR" >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR/popday"
cp popday.py "$RUNTIME_DIR/popday.py"
cp popday/*.py "$RUNTIME_DIR/popday/"

cd "$RUNTIME_DIR"
"$PYTHON_BIN" -m py_compile popday.py popday/*.py

echo "Mac Mini PopDay runtime code synced from $SOURCE_REPO to $RUNTIME_DIR"
