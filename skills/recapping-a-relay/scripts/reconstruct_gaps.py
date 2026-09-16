"""Rebuild unrecorded stretches of a relay as timed GPX tracks, one per row of gaps.csv.

Usage: python3 reconstruct_gaps.py <project_dir> [--only ID]
gaps.csv columns: id, legs, before, after [, out, title, course]
  before / after   the recordings on either side of the gap (FIT or GPX, named as in recordings.csv); the rebuilt
                   track runs from the last GPS fix of `before` to the first fix of `after`
  out              output file name (default <slug>_<id>_reconstructed.gpx), written to <project>/reconstructed/
  course           human description of the official course for this stretch (goes into the GPX description)
build_combined.py picks the outputs up automatically with the legs given here.

Method:
- Course: OpenStreetMap foot routing (routing.openstreetmap.de) between the two fixes. CHECK THE RESULT against the
  official cue sheet: routing takes the shortest footpath, not necessarily the race course. If it is wrong, put
  waypoints in a `via` column as "lat,lon;lat,lon" to force the route.
- Elevation: every 25 m, from USGS 3DEP (event.json reconstruct.elevation = "usgs", US only) or Open-Meteo
  ("open-meteo", worldwide 90 m), shifted linearly so both ends meet the neighbouring recordings' altitude.
- Time: the window between the two recordings, spread along the course with a grade-adjusted pace curve
  (1 + 2.4g + 12g^2: slower uphill, fastest near -10%), one point per second. No heart rate is invented.
Route and elevation responses are cached in <project>/cache/reconstruct/, so reruns do not call the services again.
"""
import bisect, json, os, sys, time, urllib.request
import concurrent.futures as cf
from datetime import timedelta
from xml.sax.saxutils import escape
from relaylib import Project, gps_fixes, haversine_deg, M_PER_MI, FT_PER_M, die

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args:
    sys.exit(__doc__)
P = Project(args[0])
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
CACHE = os.path.join(P.cache, "reconstruct")
UA = {"User-Agent": P.opt("reconstruct", "user_agent", default="relay-recap/1.0 (personal relay recap)")}
ELEVATION = P.opt("reconstruct", "elevation", default="usgs")


def interp(xs, ys, x):
    i = min(max(bisect.bisect_right(xs, x), 1), len(xs) - 1)
    x0, x1 = xs[i - 1], xs[i]
    f = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
    return ys[i - 1] + f * (ys[i] - ys[i - 1])


def get_json(url):
    for attempt in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)


def cached(name, fetch):
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        json.dump(fetch(), open(path, "w"))
    return json.load(open(path))


def route(points):
    coords = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in points)
    url = f"https://routing.openstreetmap.de/routed-foot/route/v1/driving/{coords}?overview=full&geometries=geojson"
    return [[lat, lon] for lon, lat in get_json(url)["routes"][0]["geometry"]["coordinates"]]


def densify(path, step):
    out, cum = [(path[0], 0.0)], 0.0
    for a, b in zip(path, path[1:]):
        d = haversine_deg(a, b)
        n = max(1, int(d // step))
        for k in range(1, n + 1):
            f = k / n
            out.append(((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])), cum + f * d))
        cum += d
    return out


def elevation_usgs(p):
    url = f"https://epqs.nationalmap.gov/v1/json?x={p[1]:.6f}&y={p[0]:.6f}&wkid=4326&units=Meters&includeDate=false"
    return float(get_json(url)["value"])


def elevations(points):
    if ELEVATION == "usgs":
        with cf.ThreadPoolExecutor(8) as ex:
            return list(ex.map(elevation_usgs, points))
    if ELEVATION == "open-meteo":
        out = []
        for k in range(0, len(points), 100):
            chunk = points[k:k + 100]
            url = ("https://api.open-meteo.com/v1/elevation?latitude=" + ",".join(f"{p[0]:.6f}" for p in chunk)
                   + "&longitude=" + ",".join(f"{p[1]:.6f}" for p in chunk))
            out += [float(e) for e in get_json(url)["elevation"]]
            time.sleep(0.2)
        return out
    die(f"reconstruct.elevation must be 'usgs' or 'open-meteo', not {ELEVATION!r}")


def sample_elevation(path):
    pts = densify(path, 25)
    elev = elevations([p for p, _ in pts])
    return [[p[0], p[1], c, e] for (p, c), e in zip(pts, elev)]


gaps = P.gaps()
if not gaps:
    die("gaps.csv is missing or empty; nothing to reconstruct")
os.makedirs(P.reconstructed, exist_ok=True)
windows = {name: (start, end) for name, _, start, end in P.recordings()}  # honor start_utc/end_utc of neighbours
for gap in gaps:
    if ONLY and gap["id"] != ONLY:
        continue
    before = gps_fixes(P.resolve(gap["before"]), *windows.get(gap["before"], (None, None)))
    after = gps_fixes(P.resolve(gap["after"]), *windows.get(gap["after"], (None, None)))
    if not before or not after:
        die(f"gap {gap['id']}: no GPS fixes in {gap['before'] if not before else gap['after']}")
    prev, nxt = before[-1], after[0]
    if nxt[0] <= prev[0]:
        die(f"gap {gap['id']}: {gap['after']} starts before {gap['before']} ends; nothing to fill")
    via = [tuple(map(float, v.split(","))) for v in (gap.get("via") or "").split(";") if v.strip()]
    path = cached(f"{gap['id']}_route.json", lambda: route([prev[1], *via, nxt[1]]))
    samples = cached(f"{gap['id']}_elevation.json", lambda: sample_elevation(path))

    # elevation: DEM shifted so both ends meet the neighbouring recordings
    dist = [s[2] for s in samples]
    dem = [s[3] for s in samples]
    length = dist[-1]
    d0 = prev[2] - dem[0] if prev[2] is not None else 0.0
    d1 = nxt[2] - dem[-1] if nxt[2] is not None else 0.0
    ele = [e + d0 + (d1 - d0) * c / length for e, c in zip(dem, dist)]

    # effort-weighted distance: grade over a ~100 m moving window
    smooth = [sum(ele[max(0, i - 2):i + 3]) / len(ele[max(0, i - 2):i + 3]) for i in range(len(ele))]
    effort = [0.0]
    for i in range(1, len(dist)):
        seg = dist[i] - dist[i - 1]
        g = max(-0.25, min(0.25, (smooth[i] - smooth[i - 1]) / seg)) if seg > 0 else 0.0
        effort.append(effort[-1] + seg * (1 + 2.4 * g + 12 * g * g))

    # one point per second between the recordings (1 s clear of each so nothing overlaps)
    t_start, t_end = prev[0] + timedelta(seconds=1), nxt[0] - timedelta(seconds=1)
    total = int((t_end - t_start).total_seconds())
    pc = [0.0]
    for a, b in zip(path, path[1:]):
        pc.append(pc[-1] + haversine_deg(a, b))
    lats, lons = [p[0] for p in path], [p[1] for p in path]
    points = []
    for s in range(total + 1):
        d = interp(effort, dist, effort[-1] * s / total)
        points.append((t_start + timedelta(seconds=s), interp(pc, lats, d), interp(pc, lons, d), interp(dist, ele, d)))

    miles = length / M_PER_MI
    title = gap.get("title") or gap["id"]
    elev_src = "USGS 3DEP" if ELEVATION == "usgs" else "Open-Meteo (Copernicus DEM)"
    name = f"{P.slug.replace('_', ' ')} {title} - Timed Reconstructed ({miles:.1f} mi)"
    course = f"Course: {gap['course']}; routed" if gap.get("course") else "Routed"
    desc = (f"Reconstructed activity, not a GPS recording. {course} on OpenStreetMap data "
            f"((c) OpenStreetMap contributors). Elevation: {elev_src}. Times: spread between the recorded handoffs "
            f"({prev[0]:%H:%M:%S} and {nxt[0]:%H:%M:%S} UTC) with a grade-adjusted pace curve. No heart rate.")
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="reconstruct_gaps.py - reconstructed from OpenStreetMap routing, elevation data '
        'and recorded handoff times" xmlns="http://www.topografix.com/GPX/1/1">',
        f"<metadata><name>{escape(name)}</name><desc>{escape(desc)}</desc>"
        f"<time>{t_start:%Y-%m-%dT%H:%M:%SZ}</time></metadata>",
        f"<trk><name>{escape(name)}</name><type>running</type><trkseg>",
        *(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{e:.1f}</ele><time>{t:%Y-%m-%dT%H:%M:%SZ}</time></trkpt>'
          for t, lat, lon, e in points),
        "</trkseg></trk></gpx>",
    ]
    out_name = P.gap_output_name(gap)
    out = os.path.join(P.reconstructed, out_name)
    open(out, "w", encoding="ascii", errors="xmlcharrefreplace").write("\n".join(body) + "\n")

    up = sum(max(0.0, b - a) for a, b in zip(smooth, smooth[1:])) * FT_PER_M
    down = sum(max(0.0, a - b) for a, b in zip(smooth, smooth[1:])) * FT_PER_M
    pace = total / miles
    straight = haversine_deg(prev[1], nxt[1]) / M_PER_MI
    print(f"{out_name}: legs {gap['legs']}, {miles:.2f} mi routed ({straight:.2f} mi straight line), "
          f"{t_start:%H:%M:%S}-{t_end:%H:%M:%S} UTC ({total // 60}:{total % 60:02d}), "
          f"avg {int(pace // 60)}:{int(pace % 60):02d}/mi, {min(ele) * FT_PER_M:.0f}-{max(ele) * FT_PER_M:.0f} ft, "
          f"+{up:.0f}/-{down:.0f} ft, altitude shift {d0 * FT_PER_M:+.0f} ft -> {d1 * FT_PER_M:+.0f} ft, {len(points)} points")
    if not 4 * 60 <= pace <= 20 * 60:
        print(f"  WARNING: {int(pace // 60)}:{int(pace % 60):02d}/mi is implausible for running; the route or the "
              "before/after files are probably wrong")
