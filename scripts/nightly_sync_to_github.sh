#!/usr/bin/env bash
set -euo pipefail

# DISABLED 1st Jul 2026.
#
# PythonAnywhere is a deploy target, not a Git source. The Mac Mini PopDay repo
# is the single source of truth and the only place that commits to GitHub. This
# script used to run `git add -A && git commit && git push` on PythonAnywhere,
# which forked PA's history away from GitHub (and would have committed database
# .bak backups). It is now a no-op.
#
# Real PopDay changes are authored on the Mac Mini and deployed to PA. If you
# want this to run again, it must NOT push from PythonAnywhere.

echo "nightly_sync_to_github.sh is disabled: PythonAnywhere no longer commits or pushes to GitHub."
exit 0
