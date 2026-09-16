---
name: recapping-a-relay
description: Use when a team relay or multi-runner event (Ragnar, Hood to Coast, ekiden, relay ultra, 200-mile team relay) has GPS watch files (FIT, GPX, Garmin/Strava/Coros exports) from several runners that should become one combined activity, a Strava upload, a shareable route map page, or a 3D flythrough video; also when some legs were not recorded, two runners' watches overlap, or one watch covers two legs.
---

# Recapping a relay

## Overview

Turn many runners' recordings into one team record: a combined FIT/GPX with one lap per leg, a Strava-safe copy,
a self-contained route map page (plus PNG snapshots), and a vertical 3D flythrough video. Everything is driven by
one project folder and scripts in `scripts/` (this skill's base directory). Rerunning is cheap: network responses are
cached in the project.

**Core principle:** the watch data is the evidence. Recorded GPS beats the team sheet and beats reconstruction;
anything rebuilt is labeled as rebuilt everywhere it appears.

## Pipeline

Run from any directory; every script takes the project folder. `S` = this skill's `scripts/` directory.

| Step | Command | Output (in `out/`) |
|---|---|---|
| 1. Set up + inventory | `python3 S/new_project.py P --sources DIR --slug NAME --timezone Area/City` | templates; a table of files by start time, overlaps, duplicates |
| 2. Fill config | edit `event.json`, `roster.csv`, `recordings.csv` (+ `gaps.csv`) | see references/project-setup.md |
| 3. Rebuild missing legs | `python3 S/reconstruct_gaps.py P` | `reconstructed/*.gpx` |
| 4. Combine | `python3 S/build_combined.py P` | `_combined.fit`, `_combined_strava.fit`, `_combined.gpx`, manifest, records |
| 5. Check | `python3 S/verify_fit.py P` | pass/fail report; must pass before anyone uploads |
| 6. Strava text | `python3 S/strava_description.py P` | `_strava_description.txt` |
| 7. Map page | `python3 S/build_map.py P` | `_route_map.html` (one file, ~1 MB) |
| 8. Snapshots | `node $(bash S/node_setup.sh P)/snapshot_map.mjs out/SLUG_route_map.html out/SLUG_route_map` | `_phone.png`, `_overview.png`, `_map.png` |
| 9. Share | see "Publishing" below | a private link |
| 10. Flythrough | `python3 S/geocode_places.py P`, `python3 S/export_flythrough.py P`, `bash S/render_flythrough.sh P preview`, then without `preview` | `_flythrough.mp4` |

Requirements: Python 3 with `fitparse` and `Pillow`; for steps 8 and 10 Node 18+, Chrome and `ffmpeg`.

## Decisions that need judgment

Read references/data-decisions.md before step 2. The short version:

- **Map files to legs from the inventory, not the file names.** A file named "Leg 11 and 12" may be a pacer on 11 whose own leg was 12. Use
  start times, distance and position against the roster. Byte-identical "copy" files are skipped automatically.
- **A long overlap (> 10 min) between two watches is not a handoff.** Usually a teammate ran along. Ask the team who
  ran the leg; give the other watch a `start_utc` (or `end_utc`) in `recordings.csv`. Short overlaps are trimmed
  automatically.
- **When the team sheet and the watches disagree, the watch wins**, unless the team corrects it. Record the planned
  runner in `plan_runner` and the reason in `note`, so the map page shows it.
- **Unrecorded legs are rebuilt, never silently skipped.** Put them in `gaps.csv`, then *check the printed route
  miles and pace* against the official leg: routing takes the shortest footpath, not the race course. Add `via`
  waypoints if it is wrong. No heart rate is ever invented.
- **Upload only `_combined_strava.fit`**, set privacy to "Only You" and type Other/Workout: a team's effort must stay
  off personal records and segment leaderboards, and heart rate from many people would read as one athlete's.

## Publishing the map page

The HTML file is self-contained, but chat and email apps strip or garble `.html` attachments and phones open them as
downloads. Share a link instead:

1. **Ask first.** The page shows every runner's name, route and times. Confirm the user wants a link before publishing.
2. Publish `out/SLUG_route_map.html` with the Artifact tool (private until the user shares it; favicon 🏃). Give the
   user the link. After a rebuild, republish the same file path so the link stays the same.
3. For group chats, send the PNG snapshots from step 8 (`_overview.png` as the summary card, `_phone.png` for scrolling).
4. Never put the page on a public host (GitHub Pages, Netlify) without explicit consent from the user.

Details: references/map-and-sharing.md. Flythrough tuning and render cost: references/flythrough.md.

## Common mistakes

| Mistake | Fix |
|---|---|
| `recordings sorted by start time give leg order ...` | A leg number is wrong, or a watch clock/timezone is off; check the inventory |
| Rebuilt leg pace like 3:00/mi or 25:00/mi | Wrong `before`/`after` file, or routing took a different road; fix `gaps.csv`, delete its cache files, rerun |
| Uploading `_combined.fit` or several files to Strava | Upload only the Strava copy, once |
| Editing files in `out/` by hand | They are regenerated; change the config and rerun |
| Full flythrough render to check framing | Use `preview` first; a full render is ~35 min and ~17 GB of frames for 36 legs |
