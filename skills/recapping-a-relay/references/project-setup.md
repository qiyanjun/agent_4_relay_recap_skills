# Project setup: config files

A project is one folder. `new_project.py` creates the templates; nothing in `out/` should be edited by hand.

```
my_relay/
  event.json        event facts and options
  roster.csv        one row per leg
  recordings.csv    which source file covers which legs
  gaps.csv          optional: legs or parts nobody recorded
  places.csv        optional: place labels for the flythrough
  reconstructed/    written by reconstruct_gaps.py (source files are never modified)
  cache/            routing, elevation, tiles, geocoding, node packages (safe to delete; rebuilt on demand)
  out/              every generated file, prefixed with the slug
```

Empty strings and `null` in `event.json` mean "not given": fill what you know, leave the rest blank.

## event.json

| Key | Required | Meaning |
|---|---|---|
| `slug` | yes | output file prefix, e.g. `CASCADE_2026` (letters, digits, `_`) |
| `event`, `team` | yes | "Cascade Relay 2026", "Trail Hawks" |
| `timezone` | yes | IANA name of the race's local time, e.g. `America/Denver`, `Europe/Berlin` |
| `sources` | yes | folder of source files, relative to the project |
| `event_short`, `dates_label` | no | map page kicker: "Cascade Relay \| August 7-8, 2026" |
| `course.summary` | no | "Mount Hood, OR to Seaside, OR" (Strava description) |
| `course.start_name` / `finish_name` | no | full names in the map page sentence |
| `course.start_short` / `finish_short` | no | under the leg strip |
| `course.start_label` / `finish_label` | no | map pins "Start \| ..."; default the short names |
| `course.finish_latlon` | no | `[lat, lon]` of the finish line if the last watch stopped short of it |
| `official.run_time`, `pace`, `overall_place`, `category`, `category_place`, `bib`, `city`, `runners`, `source` | no | the official result; omitted parts are left out of the page and the Strava text |
| `official.start`, `official.finish` | no | ISO UTC gun and finish times (`2026-08-07T14:00:00Z`); default first/last record |
| `combine.sport` | no | `generic` (default, shows as Other), `running`, `cycling`, `walking`, `hiking` |
| `combine.standing_m` | no | an early-started watch that moved less than this during the overlap was still waiting (default 50) |
| `reconstruct.elevation` | no | `usgs` (US only, 1-10 m, default) or `open-meteo` (worldwide, 90 m) |
| `reconstruct.user_agent` | no | identifies you to OpenStreetMap services (their policy asks for one) |
| `map.bbox`, `map.zoom` | no | fix the basemap area `{n,s,w,e}` and Web Mercator zoom; default course + margin, zoom fit to 2400 px |
| `map.title`, `map.description`, `map.dek` | no | override the page title, meta description and intro sentence |
| `map.footer` | no | list of HTML lines for sources and credits, e.g. `"<strong>Official result:</strong> ..."` |
| `colors` | no | `{"Runner": "#hex"}` for the flythrough; defaults come from a palette ordered for handoff contrast |
| `flythrough.*` | no | video options: `exchange`, `opening`, `title`, `label_scale`, `line_scale`, `music`, imagery colour. See flythrough.md |

## roster.csv

Required columns: `leg` (1..N with no gaps), `runner`, `miles` (official leg distance). Optional, shown when present:

| Column | Used for |
|---|---|
| `van` | map key grouping, runner table, flythrough panel |
| `rating` | leg difficulty (Easy, Hard, ...) |
| `hm_pace` | runner's half-marathon pace, a reference on the runner table |
| `from_name`, `to_name` | exchange names in the map readout |
| `gain_ft`, `loss_ft`, `max_ft`, `min_ft` | official climb per leg |
| `plan_runner` | who the team sheet planned; a difference is called out on the page |
| `note` | why (shown in the map readout) |

A runner who ran several legs appears on several rows with the same name, spelled identically.

## recordings.csv

`file,legs,start_utc,end_utc`

- `file`: exact file name in `sources/` (or `reconstructed/`).
- `legs`: `7`, or `22-23` for one recording covering consecutive legs (split by official miles).
- `start_utc` / `end_utc`: optional `YYYY-MM-DD HH:MM:SS` in UTC; records outside are dropped. Use for a watch that
  ran along with a teammate before its own leg, or kept recording in the van afterwards. Find the moment from the
  inventory, the other watch's end, or where the track leaves the exchange.

Every leg must be covered by a recording or a `gaps.csv` row, or `build_combined.py` stops with a message.

## gaps.csv

`id,legs,before,after,out,title,course,via`

- `before` / `after`: the recordings on either side (their `start_utc`/`end_utc` windows are honored). The rebuilt
  track runs from the last fix of `before` to the first fix of `after`, timed across that window.
- `out`: file name (default `<slug>_<id>_reconstructed.gpx`); `title`: short label; `course`: cue-sheet description
  for the GPX metadata; `via`: `lat,lon;lat,lon` waypoints to force the official route.
- A leg only partly unrecorded (watch started late) is a gap too: its legs column is that leg, and both the rebuilt
  part and the recording are listed; the manifest marks it "part rebuilt".
- A gap before the first recording or after the last one cannot be rebuilt this way (no anchor on one side).

Rebuilt outputs are added to the combine automatically. Cache files are `cache/reconstruct/<id>_route.json` and
`<id>_elevation.json`; delete them after changing `via` or the neighbours.

## places.csv

`name,query,tier,kind,lat,lon` - `tier` 1 always worth showing, 3 only if room; `kind` town, mountain, park, river,
road. Give `lat,lon` to pin a place Nominatim gets wrong (a summit that shares a town's name).
