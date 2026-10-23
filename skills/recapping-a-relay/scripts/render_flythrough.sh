#!/usr/bin/env bash
# Render the 3D flythrough end to end: serve out/flythrough/, capture it frame by frame with headless Chrome, encode
# to H.264. Run export_flythrough.py first (it writes out/flythrough/scene.json).
#
# Usage: bash render_flythrough.sh <project_dir> [preview]
#   preview  capture every PREVIEW_STEP'th frame (default 30, i.e. one per second of video) for a fast framing check
# Output: out/<slug>_flythrough.mp4 (or out/<slug>_flythrough_preview.mp4), plus a _timeline.json beside it.
# With event.json's flythrough.side_by_side, it renders the 3D view, then the 2D view (out/<slug>_flythrough_2d.mp4)
# on the same clock, then joins them with side_by_side.py into out/<slug>_flythrough_2d3d.mp4.
# Env: PORT=8765  CRF=22  PREVIEW_STEP=30  WORKERS=3 (Chrome instances capturing at once)  VIEW=3d|2d (one view only)
#      CHROME=/path/to/chrome  FRAMES_DIR=dir (kept)  KEEP=1 (keep temp frames)
# A full render of a 36-leg relay is ~5800 frames and ~17 GB of PNGs before encoding; WORKERS divides the capture
# time, not the disk. The imagery and terrain tiles stream from the network while capturing.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[[ $# -ge 1 ]] || { echo "usage: bash render_flythrough.sh <project_dir> [preview]" >&2; exit 2; }
PROJECT="$(cd "$1" && pwd)"
OUTDIR="$(python3 -c "import sys; sys.path.insert(0, '$HERE'); from relaylib import Project; p = Project('$PROJECT'); print(p.out)")"
SLUG="$(python3 -c "import json; print(json.load(open('$PROJECT/event.json'))['slug'])")"
WORK="$OUTDIR/flythrough"
[[ -f "$WORK/scene.json" ]] || { echo "missing $WORK/scene.json; run: python3 $HERE/export_flythrough.py $PROJECT" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg not found" >&2; exit 1; }

# side by side: run this script once per view, then join the two
if [[ -z "${VIEW:-}" ]] && python3 -c "import json, sys; sys.exit(0 if (json.load(open('$PROJECT/event.json')).get('flythrough') or {}).get('side_by_side') else 1)"; then
  for v in 3d 2d; do
    VIEW=$v FRAMES_DIR="${FRAMES_DIR:+$FRAMES_DIR/$v}" bash "$0" "$@"
  done
  exec python3 "$HERE/side_by_side.py" "$PROJECT" ${2:+"$2"}
fi
VIEW="${VIEW:-3d}"
[[ $VIEW == 3d || $VIEW == 2d ]] || { echo "VIEW must be 3d or 2d" >&2; exit 2; }
SUFFIX=$([[ $VIEW == 2d ]] && echo _2d || true)

NODE_DIR="$(bash "$HERE/node_setup.sh" "$PROJECT")"
cp "$HERE/flythrough.html" "$WORK/index.html"
ln -sfn "$NODE_DIR/node_modules" "$WORK/node_modules"

PORT="${PORT:-8765}"
CRF="${CRF:-22}"
if [[ "${2:-}" == preview ]]; then
  STEP="${PREVIEW_STEP:-30}"; OUT="$OUTDIR/${SLUG}_flythrough${SUFFIX}_preview.mp4"; RATE="30/$STEP"  # same running time as the full video
else
  STEP=1; OUT="$OUTDIR/${SLUG}_flythrough${SUFFIX}.mp4"; RATE=30
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

echo "flythrough ($VIEW) -> $OUT (every ${STEP} frame(s), ${WORKERS:-3} workers, frames in $FRAMES_DIR)"
rm -f "$FRAMES_DIR/timeline.json" "$FRAMES_DIR"/f_*.png  # a kept FRAMES_DIR must not leak an earlier run's frames
WORKERS="${WORKERS:-3}" node "$NODE_DIR/capture.mjs" "http://127.0.0.1:$PORT/index.html?view=$VIEW" "$FRAMES_DIR" "$STEP"
ffmpeg -v error -stats -y -framerate "$RATE" -i "$FRAMES_DIR/f_%05d.png" \
  -c:v libx264 -crf "$CRF" -preset medium -pix_fmt yuv420p -movflags +faststart "$OUT"
echo "$OUT: $(du -h "$OUT" | cut -f1), $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" | cut -d. -f1) s"
# Leg spans and handoff times and pixels, so later edits never have to find them in the frames. A preview runs on
# the same clock as the full video, so its timeline holds for both.
if [[ -f "$FRAMES_DIR/timeline.json" ]]; then
  TL="${OUT%.mp4}_timeline.json"
  cp "$FRAMES_DIR/timeline.json" "$TL" && echo "$TL"
fi
