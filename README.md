# Relay Recap

A Claude Code plugin that turns a team relay's GPS watch files, from many runners and many devices, into:

| Output | What it is |
|---|---|
| `<slug>_combined.fit` | One activity, one lap per leg, heart rate kept: the full team record |
| `<slug>_combined_strava.fit` | The same with no heart rate and activity type Other: the file to upload |
| `<slug>_combined.gpx` | One named track per leg, "Leg 7 \| Runner" |
| `<slug>_strava_description.txt` | Title and description: result, notes, every leg's start, miles, time and runner |
| `<slug>_legs_manifest.csv`, `<slug>_merged_records.csv` | Per-leg and per-second data, tagged with runner and source |
| `<slug>_route_map.html` | Self-contained route map page (map, leg strip, leg table, elevation through the night, runners) |
| `<slug>_route_map_{phone,overview,map}.png` | Snapshots of that page for group chats and slides |
| `<slug>_flythrough.mp4` | Vertical 3D flythrough over satellite imagery and terrain, leg by leg |

It also rebuilds legs nobody recorded (OpenStreetMap routing, DEM elevation, grade-adjusted timing, clearly
labeled), trims handoff overlaps, splits one watch covering two legs, and handles a teammate running along.

Generalized from a hand-made recap of a real team relay, and checked against it before that data was removed.

## Install

In Claude Code:

```
/plugin marketplace add qiyanjun/agent_4_relay_recap_skills
/plugin install relay-recap@relay-recap
```

or from a terminal:

```
claude plugin marketplace add qiyanjun/agent_4_relay_recap_skills
claude plugin install relay-recap@relay-recap
```

Restart Claude Code (or start a new session) so the skill loads. Update later with
`claude plugin marketplace update relay-recap`. From a local clone, use the clone's path instead of
`qiyanjun/agent_4_relay_recap_skills`.

## How to use

Put every runner's files (FIT or GPX exports from Garmin, Coros, Strava, ...) in one folder, then ask Claude in plain
words. The `recapping-a-relay` skill loads automatically; you can also name it: "use the recapping-a-relay skill".

Example requests:

- "Here are our relay team's watch files in ~/Downloads/relay. Combine them into one activity with a lap per leg."
- "Make the Strava upload file and description for our relay."
- "Legs 7 and 18 weren't recorded; rebuild them from the course."
- "Build the route map page and publish it as a private link." / "Make PNG snapshots of the map for our group chat."
- "Render the 3D flythrough video, preview first."

What Claude will do and ask you for:

1. Create a project folder and list your files by start time, flagging duplicates and suspicious overlaps.
2. Ask for the roster (leg, runner, official miles; optionally van, difficulty, exchange names), the time zone, and
   the official result if you have it, then match each file to its legs with you.
3. Ask who really ran a leg when the team sheet and the watches disagree, or when two watches overlap for a long time.
4. Rebuild unrecorded legs, combine, and check the FIT files; you review the printed rebuilt-leg miles and pace.
5. Build the Strava copy and text, the map page and snapshots, and, if you want, the flythrough video.
6. Ask before publishing the map as a private link; never post it publicly without your consent.

Upload only `<slug>_combined_strava.fit` to Strava, with privacy "Only You" and type Other. Details of every option
are in `skills/recapping-a-relay/references/`.

## Requirements

- Python 3.10+ with `fitparse` and `Pillow` (`pip install fitparse pillow`)
- For snapshots and the flythrough: Node.js 18+, Google Chrome (or set `CHROME`), `ffmpeg`
- Network for the first run of routing, elevation, basemap tiles, geocoding and flythrough imagery (all cached)

## Quick start without Claude

```
S=skills/recapping-a-relay/scripts
python3 $S/new_project.py my_relay --sources ~/relay_files --slug RELAY_2026 --timezone America/Denver
# fill my_relay/event.json, roster.csv, recordings.csv (+ gaps.csv)
python3 $S/reconstruct_gaps.py my_relay        # only if gaps.csv has rows
python3 $S/build_combined.py my_relay
python3 $S/verify_fit.py my_relay
python3 $S/strava_description.py my_relay
python3 $S/build_map.py my_relay
node "$(bash $S/node_setup.sh my_relay)/snapshot_map.mjs" my_relay/out/RELAY_2026_route_map.html my_relay/out/RELAY_2026_route_map
python3 $S/geocode_places.py my_relay && python3 $S/export_flythrough.py my_relay
bash $S/render_flythrough.sh my_relay preview   # then without "preview" for the full video
```

Config reference: `skills/recapping-a-relay/references/project-setup.md`.

## Layout

```
.claude-plugin/          plugin.json, marketplace.json
skills/recapping-a-relay/
  SKILL.md               when to use, pipeline, judgment calls, publishing
  references/            project-setup, data-decisions, map-and-sharing, flythrough
  scripts/               relaylib.py (shared), one script per step, map_template.html, flythrough.html, Node capture tools
tests/                   synthetic end-to-end tests (made-up relay, no personal data), plugin validator
```

## Tests

```
python3 -m pytest tests/ -q          # synthetic 6-leg relay, offline (the map render test needs Node packages)
python3 tests/validate_plugin.py     # manifests, skill metadata, scripts
```

The synthetic relay (`tests/synthetic.py`) exercises every combining rule: a watch running past the handoff, an
early-started watch standing at the exchange, one watch for two legs, a rebuilt leg, a teammate running along, a
Strava-style GPX and a duplicate file.

## Privacy

Relay files carry teammates' names, routes, times and heart rate. The Strava copy drops heart rate; the skill publishes
the map page only as a private link after asking, and never to a public host without consent. Build outputs and caches are git-ignored; keep real team data out of this repository.

## Data sources and credits

OpenStreetMap routing (routing.openstreetmap.de) and Nominatim geocoding, (c) OpenStreetMap contributors; USGS 3DEP
elevation and The National Map imagery; Open-Meteo elevation; Esri World Hillshade and World Imagery; CARTO labels;
AWS Terrain Tiles; EOX Sentinel-2 cloudless; MapLibre GL JS; Puppeteer.

## Other ways to do it, and when to choose them

No off-the-shelf tool handles a relay from start to finish: merging many runners' files, splitting and trimming legs,
rebuilding unrecorded legs, and labeling each leg with its runner. This plugin exists for that case, and it takes the
most setup. If your needs are smaller, or different, one of these may be the better choice.

### Video and replay

| Option | Choose it when | Limits for a relay |
|---|---|---|
| **Strava Flyover** (3D aerial recap built into Strava) | You want a video of **one** leg or a short relay, with no setup and each runner uploading their own activity | Works only for activities under 100 km, so a 200-mile combined relay is too long. Each leg becomes its own video, with no team view |
| **Strava Flyby** (labs.strava.com/flyby) | Everyone already uploads to Strava, and you want a quick replay of who was near whom, such as handoffs or nearby teams | Nothing is combined: legs run one after another, so you mostly see one dot at a time. Each runner's privacy settings must allow Flyby. Missing legs stay missing, and you can't export the replay |
| **Relive** | You want a phone-made video of one runner's activity in minutes | Can't merge activities into one video. No runner names, colors, or rebuilt-leg labels |
| **TrailReplay**, **AvoMap** | You want several tracks played as one journey video with little effort, and per-runner detail doesn't matter | They combine tracks but have no per-runner features such as runner names, colors, a legend, or rebuilt tags |
| **This plugin's flythrough** (step 10) | You want the whole relay told leg by leg in 3D, with runner colors, clock, place labels, and rebuilt tags, and you want to re-render it after corrections | A full render takes about 35 min and about 17 GB of temporary frames for 36 legs. Needs Node, Chrome, and ffmpeg |

### 2D animation instead of a 3D flythrough

A flat, top-down animation is often the better recap. Choose 2D when:

- The course is flat, or the video is for slides, where tilted terrain adds little and can hide legs.
- You want the whole course visible at once, so every leg can be compared.
- You need a render that is fast, small enough for chat apps, or runs without a GPU, satellite tiles, or a network connection.
- You want a landscape video for a screen or a talk rather than a vertical video for phones.

All of these can use this plugin's outputs as input: `_combined.gpx` (one named track per leg) or
`_merged_records.csv` (every point with time, leg, runner, and whether it was rebuilt).

| Option | Choose it when | Trade-off |
|---|---|---|
| **GPX Animator** (free desktop app, Java, command line) | You want a ready-made 2D track animation over imagery and are comfortable scripting around it. Rendering one scene per leg and joining them with ffmpeg works well | No 3D terrain, and the info panel can't be customized, so the clock panel and legend must be drawn separately. It has rendering quirks, such as dark beads where outlines overlap. Needs Java 17 |
| **Python frames** (matplotlib with a tile basemap such as `contextily`, then ffmpeg) | You want full control of layout, labels, and colors, with no browser, and a render that works offline once the tiles are cached | You write the animation yourself: camera framing, label placement, and pacing. Simple, but slower to polish |
| **A flat browser map captured frame by frame** (MapLibre or Leaflet, with this plugin's `capture.mjs`) | You want 2D but with this plugin's pipeline: leg blocks, clock panel, legend, and the same frame-by-frame capture into ffmpeg | Setting `"pitch": 0` in `event.json` flattens the camera for the leg blocks, but terrain still loads and the intro and outro views stay tilted. That path is untested; a true 2D page would be a new, simpler template |
| **QGIS Temporal Controller** (free desktop GIS) | Someone on the team knows GIS and wants to style the map by hand, for example print-quality cartography or custom layers | Hands-on and not automated: load the points with their time field, style them, export the frames, and join them with ffmpeg. Repeat by hand after every data change |
| **kepler.gl** trip layer (browser) | You want an interactive 2D or 3D animated replay to explore or screen-record, with little code | Needs the tracks converted to GeoJSON with timestamps. Runner labels and a clock panel are limited, and making a video needs a screen recorder or its video-export add-on |

### Combining files

| Option | Choose it when | Limits for a relay |
|---|---|---|
| **GOTOES** (the combiner Strava's help center points to), **FIT File Tools** combiner | A few clean files that don't overlap just need to become one upload | They join files in time order. They don't trim handoff overlaps, split a watch that covered two legs, handle a teammate running along, rebuild missing legs, or label legs by runner |
| **Leave the activities separate** | The team only wants each runner's stats and kudos, or doesn't want one activity holding everyone's routes | No team record or combined time, and unrecorded legs are simply absent. The map page and video can still be built from the separate files |

### During the race

| Option | Choose it when |
|---|---|
| **RACEMAP**, **RunnerBeam**, Garmin LiveTrack, or the race's own tracker | Vans and family want to see where the runner is *during* the race. These are live-location tools, not recaps; use this plugin afterward |

**A rough guide.** Every leg recorded, and the team just wants to watch? Use Flyover for single legs or Flyby for a
quick replay. Files only need joining, with no overlaps or gaps? Use GOTOES. Want a quick, flat video of the whole course? Use GPX Animator or Python frames on `_combined.gpx`. Legs missing, watches overlapping,
teammates running along, or you want a lasting team record, map page, and full-relay video? Use this plugin.

Sources: Relive, merging activities
(support.relive.com/kb/guide/en/can-i-merge-activities-into-one-relive-video-ZLn6zsovYn); Strava Flyover
(support.strava.com/hc/en-us/articles/19900004650125-Flyover); Strava, merge or combine activities
(support.strava.com/en-us/articles/15401839-merge-or-combine-activities); GOTOES
(gotoes.org/strava/Combine_GPX_TCX_FIT_Files.php); FIT File Tools (fitfiletools.com/combiner); TrailReplay
(trailreplay.com); AvoMap (avomap.com/route-maps); GPX Animator (gpx-animator.app); QGIS Temporal Controller
(docs.qgis.org); kepler.gl (kepler.gl); RACEMAP
(docs.racemap.com/live-tracking/map); RunnerBeam (runnerbeam.com).
