#!/usr/bin/env sh
# ChatTokenOptimizer installer for macOS / Linux.
# Usage: ./install.sh [--dry-run] [--uninstall] [--keep-settings] [--force-skills]
set -e
here=$(cd "$(dirname "$0")" && pwd)
py=$(command -v python3 || command -v python || true)
[ -n "$py" ] || { echo "Python 3.9+ not found" >&2; exit 1; }
exec "$py" "$here/install.py" "$@"
