# Route map page and sharing

## What the page shows

- Masthead: event, team, official time/pace/place (or elapsed time when there is no official result), tracked miles.
- Leg strip sized by official miles: recorded, partial, rebuilt (hatched), or no file.
- Map over shaded relief (light and dark themes) with leg plates, start/finish pins, a readout for the hovered leg,
  and the leg table beside it; hover or tap links map, strip, table and chart.
- Elevation against clock time with night shaded (sunset/sunrise computed at the middle of the course).
- Runners table: legs, miles, time, pace (grey when it includes rebuilt legs).
- Footer: `map.footer` lines, track summary, basemap credit.

Units are miles and feet. Optional roster columns (van, rating, hm_pace, climb, exchange names) appear only when
present. Up to 13 runner colors are defined per theme; more runners reuse colors, so names stay on every label.

## Basemap

Esri World Hillshade tinted per theme with CARTO label tiles ((c) OpenStreetMap contributors (c) CARTO), downloaded
once into `cache/tiles`. Offline, missing tiles are blank and the script warns; rerun online for the full basemap.
Set `map.bbox`/`map.zoom` to keep the exact framing across rebuilds.

## Sharing

The page is one HTML file of about 1 MB (data and basemaps inlined; only the web font loads from Google Fonts, with a
system fallback). Sending the file works poorly: mail and chat apps strip or block `.html`, messaging apps show a file
icon, and phones open it as a download or raw code. Choose by audience:

| Audience | Share |
|---|---|
| The team, any device | Private artifact link (below) |
| A group chat | `_overview.png` (summary card) and `_phone.png` (whole page, phone width) |
| Slides or a print-out | `_map.png` (map with leg table, 2x) |
| Archive | the HTML file itself; it works offline in any browser |

### Publishing a private link (Claude Code Artifact tool)

1. Confirm with the user first: the page contains every runner's name, GPS route and times.
2. Publish `out/<slug>_route_map.html` with the Artifact tool, favicon 🏃, a one-sentence description such as
   "Team X's Relay 2026: combined route, legs, runners and elevation". It is private until the user shares it.
3. Give the user the link. After rebuilding, publish the same file path again: the link is updated in place.
4. Public hosting (GitHub Pages, Netlify, Cloudflare Pages) only with the user's explicit consent, ideally after the
   team agrees; offer the private link first.

### Snapshots

`snapshot_map.mjs` renders the light theme in headless Chrome:
- `_phone.png`: 430 px wide at 2x, full page; the leg table is expanded, the Rating and HM pace columns are hidden
  and the table is tightened so nothing is cut off in a picture.
- `_overview.png`: 1440 px wide at 2x, masthead to leg strip.
- `_map.png`: 1440 px wide at 2x, map and leg table.
It exits non-zero if the page throws a JavaScript error.
