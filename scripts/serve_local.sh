#!/usr/bin/env bash
# Serve the page and its backend on one port, the way the demo expects.
#
#   scripts/serve_local.sh            # http://127.0.0.1:5173
#   PORT=8000 scripts/serve_local.sh
#
# PYTHONNOUSERSITE=1 is not optional on a shared host: packages in
# ~/.local (numpy, scipy, scikit-learn) otherwise load ahead of the env's own
# and the server runs a mix of two installs.
#
# Keys are read from a file OUTSIDE the repo - $KRINO_ENV_FILE, default
# ~/.config/modal-hackathon/ai-gateway.env (keep it mode 600) - and exported
# into this process only. Nothing secret sits in the working tree or on the
# command line. Already-exported variables win over the file.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONNOUSERSITE=1
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${TMPDIR:-/tmp}/krino-mpl}"

env_file="${KRINO_ENV_FILE:-$HOME/.config/modal-hackathon/ai-gateway.env}"
if [ -f "$env_file" ]; then
  while IFS='=' read -r key value; do
    case "$key" in ''|\#*) continue ;; esac
    key="${key// /}"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    if [ -z "${!key:-}" ]; then export "$key=$value"; fi
  done < "$env_file"
else
  echo "no key file at $env_file: running with whatever is exported (or offline)" >&2
fi
exec "${PYTHON:-python}" -m scrnapipeline.cli serve --host "${HOST:-127.0.0.1}" --port "${PORT:-5173}"
