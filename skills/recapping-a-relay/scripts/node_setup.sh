#!/usr/bin/env bash
# Install the Node packages used by the snapshot and flythrough steps (puppeteer-core, maplibre-gl) into
# <project>/cache/node once, copy the .mjs tools next to them, and print that directory.
# puppeteer-core does not download a browser: it drives the installed Chrome (set CHROME=/path if not found).
# Usage: bash node_setup.sh <project_dir>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ $# -eq 1 ]] || { echo "usage: bash node_setup.sh <project_dir>" >&2; exit 2; }
command -v node >/dev/null || { echo "node not found: install Node.js 18+" >&2; exit 1; }
DIR="$(cd "$1" && pwd)/cache/node"
mkdir -p "$DIR"
cp "$HERE/package.json" "$DIR/package.json"
if [[ ! -d "$DIR/node_modules/puppeteer-core" || ! -d "$DIR/node_modules/maplibre-gl" ]]; then
  (cd "$DIR" && npm install --no-audit --no-fund --loglevel=error >&2)
fi
cp "$HERE"/*.mjs "$DIR/"
echo "$DIR"
