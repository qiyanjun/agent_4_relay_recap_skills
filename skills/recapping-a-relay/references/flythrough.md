# 3D flythrough video

A vertical (1080x1920) MapLibre scene over satellite imagery and terrain, captured frame by frame in headless Chrome.

## Structure

- Intro: wide shot with region names (states/provinces), easing into the first block.
- Legs in blocks of `block_size` (default 5): the camera holds one fixed, pitched pose per block, fitted so every leg
  of the block sits between the clock panel and the legend. The runner dot draws the leg in the runner's color;
  finished legs stay drawn; the rest of the course is a thin grey line with a dark casing.
- Each leg lasts `base_s + per_mi_s * miles` seconds (3.0 + 0.35/mi), so long legs do not rush and short ones stay
  readable. Between blocks the race clock freezes while the camera moves (1.5 s).
- Clock panel: leg, runner, rebuilt tag, miles, rating, pace, van, block range, local time.
- Legend: every runner with their legs, current runner highlighted; imagery and terrain credits.
- Place labels: at most five per block, chosen by projecting against that block's camera, nudged instead of dropped
  when they collide. Region border crossings get a "UT | NV state line" style label.
- Outro: back to the wide shot at the finish.

## Steps

1. `python3 geocode_places.py P` - needs `places.csv` (optional). Check the printed table: every place's distance
   from the course; wrong matches are dropped at `places_max_mi` (14). Pin wrong ones with lat/lon in the CSV.
2. `python3 export_flythrough.py P [first-last]` - writes `out/flythrough/scene.json`. A range renders only those legs
   with earlier legs drawn as context.
3. `bash render_flythrough.sh P preview` - one frame per second of video (about a minute of work). Look at the
   printed block zooms and place picks and at the preview video before committing to a full render.
4. `bash render_flythrough.sh P` - the full video. For 36 legs: ~5800 frames, ~35 minutes, ~17 GB of temporary PNGs
   (set `FRAMES_DIR` to a disk with room). Tiles stream from the network during capture.

Env: `PORT` (default 8765; pick another if busy), `CHROME`, `CRF` (quality, default 22), `KEEP=1`.

## Options (event.json "flythrough")

| Key | Default | Notes |
|---|---|---|
| `imagery` | `usgs` | `usgs`: US only, public domain, leaf-on summer imagery. `esri`: worldwide. `eox`: Sentinel-2 cloudless, worldwide, non-commercial license. Or an XYZ URL with `imagery_credit`. |
| `block_size` | 5 | legs per camera pose; fewer = closer framing, more transitions |
| `pitch` | 50 | camera tilt in degrees |
| `exaggeration` | 1.8 | terrain height multiplier; wide shots flatten relief |
| `base_s`, `per_mi_s` | 3.0, 0.35 | pacing |
| `width`, `height` | 1080, 1920 | 1920x1080 for landscape |
| `places_max_mi` | 14 | geocode_places.py distance filter |

Colors: runners get a palette ordered so consecutive runners contrast (validated for color-vision deficiency on dark
imagery); override per runner with `colors` in event.json.

## Imagery licensing

USGS National Map imagery is public domain (credit requested; included). OpenStreetMap's tile servers forbid bulk
downloads, so they are not used for imagery. Esri World Imagery has usage terms; EOX Sentinel-2 cloudless is
CC BY-NC-SA. The credit line in the legend follows the chosen source.
