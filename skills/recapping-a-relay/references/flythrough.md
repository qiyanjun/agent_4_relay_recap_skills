# 3D flythrough video

A vertical (1080x1920) MapLibre scene over satellite imagery and terrain, captured frame by frame in headless Chrome.

## Structure

- Opening: `flyin` (default) is a wide shot with region names easing into the first block; `tilt` starts on a flat
  overhead map and tilts up into the first block's camera, so the terrain rises out of the map; `hold` does not move
  the camera at all. With `title` set, an opening page plays over whichever one is chosen.
- Legs in blocks of `block_size` (default 5): the camera holds one fixed, pitched pose per block, fitted so every leg
  of the block sits between the clock panel and the legend. The runner dot draws the leg in the runner's color;
  finished legs stay drawn; the rest of the course is a thin grey line with a dark casing.
- Each leg lasts `base_s + per_mi_s * miles` seconds (3.0 + 0.35/mi), so long legs do not rush and short ones stay
  readable. Between blocks the race clock freezes while the camera moves (1.5 s).
- Clock panel: leg, runner, rebuilt tag, miles, rating, pace, van, block range, local time.
- Legend: every runner with their legs, current runner highlighted; imagery and terrain credits.
- Place labels: at most five per block, chosen by projecting against that block's camera, nudged instead of dropped
  when they collide. Region border crossings get a "UT | NV state line" style label.
- Exchanges: with `exchange` set, each handoff is marked - a `firework` (a flash, a shockwave ring and 30 sparks)
  or a `baton` (a streak running along the course through the exchange, its trail morphing from the outgoing
  runner's colour into the incoming runner's). Both fire when the incoming leg starts, which is after any block
  transition, so neither smears across a moving camera.
- Outro: back to the wide shot at the finish.

## Steps

1. `python3 geocode_places.py P` - needs `places.csv` (optional). Check the printed table: every place's distance
   from the course; wrong matches are dropped at `places_max_mi` (14). Pin wrong ones with lat/lon in the CSV.
2. `python3 export_flythrough.py P [first-last]` - writes `out/flythrough/scene.json`. A range renders only those legs
   with earlier legs drawn as context.
3. `bash render_flythrough.sh P preview` - one frame per second of video (about a minute of work). Look at the
   printed block zooms and place picks and at the preview video before committing to a full render.
   `export_flythrough.py` ends by listing who runs each leg; confirm it with the team before the full render.
4. `bash render_flythrough.sh P` - the full video. For 36 legs: ~5800 frames, ~35 minutes, ~17 GB of temporary PNGs
   (set `FRAMES_DIR` to a disk with room). Tiles stream from the network during capture.
5. `python3 add_music.py P` - optional: lays the background track over it and sets both files' cover image.

Every render also writes `_flythrough_timeline.json` (or `_flythrough_preview_timeline.json`) beside the video:
`fps`, `frames`, `duration_s`, `intro_end_s`, `outro_start_s`; `blocks` (legs, start and end seconds); `legs`
(leg, runner, colour, start and end in seconds and frames); and `handoffs` (from and to leg and runner, the time
and frame the incoming leg starts, and the exchange point's `x`/`y` pixel under that block's fixed camera, which is
where the firework or baton fires). The music copy has the same timing.

Env: `PORT` (default 8765; pick another if busy), `WORKERS` (default 3), `VIEW` (`3d` or `2d`, one view only),
`CHROME`, `CRF` (quality, default 22), `KEEP=1`.

**Rendering faster.** `WORKERS=n` captures with n windows of one Chrome, each taking a contiguous stretch of
frames. The first window fits the cameras and label placements and hands them to the others, so their frames
match a one-window render (the first frame of each stretch is drawn once and discarded, because markers are
pinned before the terrain under them loads). It divides the capture time, not the ~17 GB of frames. There is no
partial re-render: every frame shows the legend and all finished legs, so a corrected runner or track changes
frames from the start of the video.

A stray `python3 -m http.server` left on the port from an earlier run answers `/` with a 404 rather than refusing
the connection, so the "port in use" check reads it as free and the render then fetches `scene.json` from the wrong
server. If `capture.mjs` dies on `"<!DOCTYPE " is not valid JSON`, that is what happened: pass another `PORT`.

## Side by side: 2D and 3D

With `side_by_side: true`, `render_flythrough.sh` renders the 3D view, then the 2D view (`?view=2d`, or `VIEW=2d`),
then runs `side_by_side.py`, which joins them into `_flythrough_2d3d.mp4`: 2D on the left, 3D on the right, a
header naming each half (`panel_titles`: two `[title, subtitle]` pairs; the defaults are "2D Satellite Map" and
"3D Terrain Flythrough"). The 2D view is the same scene straight down and north-up, without terrain, framed on
each leg and gliding to the next in the first 0.9 s of a leg; its town labels are picked per leg. Everything that
sets the timing is shared, so the halves stay frame-for-frame in step: same clock, same handoffs, same fireworks.
`side_by_side.py` compares the two timelines first and refuses to join halves rendered from different rosters.
`add_music.py` gives the side-by-side video the same soundtrack as the 3D one.

## Editing a finished video

Asked to add something to a video that is already rendered (fireworks, music, a caption, a side-by-side with
another video), in this order:

1. **If the project still has its config, change `event.json` and re-render** - `exchange`, `title`, `music`,
   `music_start` are all options. Everything then lines up with the map, clock and legend by construction.
2. **Otherwise read the timeline file** for every leg's span and every handoff's moment and pixel. Do not detect
   handoffs from the frames: on a real 36-leg video that went wrong several ways at once (a handoff off by a whole
   leg, a header offset counted twice, bursts on the wrong spot).
3. **A video with no timeline** (an older render, another app's export): detect from the frames as a last resort,
   then check every handoff on a contact sheet of crops before a full encode.

Always write a new file rather than over the input, and copy the inputs into the project first: users rename
and move videos while an edit is running.

## Background music

`add_music.py P` muxes `flythrough.music` over the finished video, writing `_flythrough_music.mp4` beside the
silent file, and gives both a cover image. It stream-copies the video, so it takes seconds.

A song is almost never the video's length. Cutting its tail chops the last chorus off mid-word; cutting its head
loses an instrumental intro nobody misses. So the window is anchored on its **end** - the track's last audible
moment, measured from its loudness profile - and the start is whatever the video's length asks for. The song's own
ending then lands on the video's last frame and nothing sung is lost. A track shorter than the video is padded with
silence instead. Teams often want their song from its first bar instead: `"music_start": 0` in event.json (or
`--start 0`) plays it from the beginning and cuts it where the video ends, so ask. `--start` forces any offset, `--volume`, `--fade-in`, `--fade-out` and `--poster` tune the rest.

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
| `exchange` | `none` | `firework`, `baton` or `none`: what marks each handoff |
| `opening` | `flyin` | `flyin`, `tilt` or `hold`: how the opening camera behaves |
| `title` | `false` | `true` builds an opening page from event.json; a list of `[class, text]` rows replaces it |
| `title_seconds` | 6.9 | how long the opening runs when it carries a title (without one it is 3.0) |
| `title_dark` | `false` | a near-black title page instead of the blue sky gradient |
| `leg_label_pop` | `false` | the "Leg N \| Runner" label pops in as each leg begins |
| `label_scale` | 1.0 | multiplies every label; 1.1-1.15 is a good bump for phone viewing |
| `line_scale` | 1.0 | multiplies the route line widths and the runner dot; 1.5 reads well on a phone |
| `imagery_saturation`, `imagery_brightness_min`, `imagery_contrast` | 0 | recolour the tiles; see below |
| `music` | none | an audio file, relative to the project, for `add_music.py` |
| `side_by_side` | `false` | also render the 2D view and join both into `_flythrough_2d3d.mp4` |
| `panel_titles` | 2D / 3D defaults | `[["title", "subtitle"], ["title", "subtitle"]]` for the 2D and 3D halves |
| `music_start` | none | seconds into the track to start; `0` = from its beginning. Unset anchors the song's end on the last frame |

## Making the map greener

The satellite tiles go through MapLibre's raster paint. `imagery_saturation` up to about 0.7 with
`imagery_brightness_min` around 0.25 (which lifts the shadowed slopes so the green reads there too) and
`imagery_contrast` about -0.08 gives a vivid green without wrecking the bare rock. Past roughly 0.85 saturation the
image goes neon and bare ground turns orange. Raising `imagery_brightness_min` alone washes the colour out toward
grey, so pastel needs the saturation raised at the same time.

## The opening title page

`title: true` builds it from `event.json`: the kicker from `event_short`, the four-digit year out of `event`, the
team name one word per line sized so the longest word spans the frame, a rule, `__LEGS__ legs | __MILES__ miles |
__RUNNERS__ runners`, and the official result. Supply your own rows instead by setting `title` to a list of
`[class, text]` pairs, using the classes `t-kicker`, `t-year`, `t-team`, `t-rule`, `t-stats` and `t-result`.

Every line is at **full opacity on frame 0**, and the entrance is carried by each line settling into position and
scale rather than fading up from nothing. That is deliberate: a player shows frame 0 while a video sits unplayed,
so a page that fades up from empty gives the file a blank first frame. `add_music.py` also attaches a composed
frame as cover art, but cover art only covers Finder and media libraries - players that draw frame 0 need frame 0
to be right.

Colors: runners get a palette ordered so consecutive runners contrast (validated for color-vision deficiency on dark
imagery); override per runner with `colors` in event.json.

## Imagery licensing

USGS National Map imagery is public domain (credit requested; included). OpenStreetMap's tile servers forbid bulk
downloads, so they are not used for imagery. Esri World Imagery has usage terms; EOX Sentinel-2 cloudless is
CC BY-NC-SA. The credit line in the legend follows the chosen source.
