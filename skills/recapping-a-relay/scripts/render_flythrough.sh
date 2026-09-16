#!/usr/bin/env bash
# Render the 3D flythrough end to end: serve out/flythrough/, capture it frame by frame with headless Chrome, encode
# to H.264. Run export_flythrough.py first (it writes out/flythrough/scene.json).
#
# Usage: bash render_flythrough.sh <project_dir> [preview]
#   preview  capture every PREVIEW_STEP'th frame (default 30, i.e. one per second of video) for a fast framing check
# Output: out/<slug>_flythrough.mp4 (or out/<slug>_flythrough_preview.mp4)
# Env: PORT=8765  CRF=22  PREVIEW_STEP=30  CHROME=/path/to/chrome  FRAMES_DIR=dir (kept)  KEEP=1 (keep temp frames)
# A full render of a 36-leg relay is ~5800 frames: roughly 35 min and 17 GB of PNGs before encoding. The imagery and
# terrain tiles stream from the network while capturing.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ $# -ge 1 ]] || { echo "usage: bash render_flythrough.sh <project_dir> [preview]" >&2; exit 2; }
PROJECT="$(cd "$1" && pwd)"
OUTDIR="$(python3 -c "import sys; sys.path.insert(0, '$HERE'); from relaylib import Project; p = Project('$PROJECT'); print(p.out)")"
SLUG="$(python3 -c "import json; print(json.load(open('$PROJECT/event.json'))['slug'])")"
WORK="$OUTDIR/flythrough"
[[ -f "$WORK/scene.json" ]] || { echo "missing $WORK/scene.json; run: python3 $HERE/export_flythrough.py $PROJECT" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg not found" >&2; exit 1; }

NODE_DIR="$(bash "$HERE/node_setup.sh" "$PROJECT")"
cp "$HERE/flythrough.html" "$WORK/index.html"
ln -sfn "$NODE_DIR/node_modules" "$WORK/node_modules"

PORT="${PORT:-8765}"
CRF="${CRF:-22}"
if [[ "${2:-}" == preview ]]; then
  STEP="${PREVIEW_STEP:-30}"; OUT="$OUTDIR/${SLUG}_flythrough_preview.mp4"; RATE=$(( 30 / STEP > 0 ? 30 / STEP : 1 ))
else
  STEP=1; OUT="$OUTDIR/${SLUG}_flythrough.mp4"; RATE=30
fi

# Only a scratch directory we created ourselves is ours to delete afterwards.
if [[ -n "${FRAMES_DIR:-}" ]]; then
  OWN_FRAMES=0; mkdir -p "$FRAMES_DIR"
else
  OWN_FRAMES=1; FRAMES_DIR="$(mktemp -d "${TMPDIR:-/tmp}/relay-flythrough.XXXXXX")"
fi
if [[ $STEP == 1 ]]; then
  FREE_GB=$(df -Pk "$FRAMES_DIR" | awk 'NR==2 {print int($4 / 1048576)}')
  (( FREE_GB >= 20 )) || echo "WARNING: only ${FREE_GB} GB free for frames in $FRAMES_DIR; a full render can need ~17 GB (set FRAMES_DIR)" >&2
fi

SERVER=""
if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
  echo "port $PORT is already in use; set PORT to a free one" >&2; exit 1
fi
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$WORK" >/dev/null 2>&1 &
SERVER=$!
for _ in $(seq 40); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/index.html" 2>/dev/null && break
  sleep 0.25
done
cleanup() {
  [[ -n "$SERVER" ]] && kill "$SERVER" 2>/dev/null || true
  [[ $OWN_FRAMES == 1 && "${KEEP:-0}" != 1 ]] && rm -rf "$FRAMES_DIR" || true
}
trap cleanup EXIT

echo "flythrough -> $OUT (every ${STEP} frame(s), frames in $FRAMES_DIR)"
node "$NODE_DIR/capture.mjs" "http://127.0.0.1:$PORT/index.html" "$FRAMES_DIR" "$STEP"
ffmpeg -v error -stats -y -framerate "$RATE" -i "$FRAMES_DIR/f_%05d.png" \
  -c:v libx264 -crf "$CRF" -preset medium -pix_fmt yuv420p -movflags +faststart "$OUT"
echo "$OUT: $(du -h "$OUT" | cut -f1), $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" | cut -d. -f1) s"
