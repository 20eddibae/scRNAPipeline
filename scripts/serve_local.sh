#!/usr/bin/env bash
# Serve the page and its backend on one port, the way the demo expects.
#
#   scripts/serve_local.sh            # http://127.0.0.1:5173
#   PORT=8000 scripts/serve_local.sh
#
# PYTHONNOUSERSITE=1 is not optional on a shared host: packages in
# ~/.local (numpy, scipy, scikit-learn) otherwise load ahead of the env's own
# and the server runs a mix of two installs. Keys come from .env (see
# config.py); nothing secret is passed on the command line.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONNOUSERSITE=1
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${TMPDIR:-/tmp}/krino-mpl}"
exec "${PYTHON:-python}" -m scrnapipeline.cli serve --host "${HOST:-127.0.0.1}" --port "${PORT:-5173}"
