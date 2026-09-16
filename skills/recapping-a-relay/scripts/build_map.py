"""Build the self-contained route map page: out/<slug>_route_map.html (data and basemaps inlined, one file).

Usage: python3 build_map.py <project_dir>
Inputs: event.json, roster.csv, out/<slug>_legs_manifest.csv and out/<slug>_merged_records.csv (build_combined.py),
map_template.html (next to this script).
Also writes out/<slug>_map_data.json and out/<slug>_basemap_{light,dark}.webp.

Basemap: Esri World Hillshade relief tinted light/dark, with CARTO label tiles ((c) OpenStreetMap contributors) on
top, in Web Mercator pixel space. Tiles are cached in <project>/cache/tiles. The area is event.json map.bbox, or the
course plus a margin; the zoom is map.zoom, or the largest that keeps the image within 2400 px.
Optional roster columns shown when present: van, rating, hm_pace, from_name, to_name, gain_ft, loss_ft, plan_runner, note.
"""
import base64, html, json, math, os, sys, time, urllib.request
from datetime import datetime
from PIL import Image, ImageOps
from relaylib import Project, utc_naive, legs_label, nights_between, die

if len(sys.argv) != 2:
    sys.exit(__doc__)
HERE = os.path.dirname(os.path.abspath(__file__))
P = Project(sys.argv[1])
E = P.event
roster, manifest = P.roster(), P.manifest()
records = P.merged_records()
TILES = os.path.join(P.cache, "tiles")
UA = P.opt("reconstruct", "user_agent", default="relay-recap/1.0 (personal relay recap)")
MAX_PX = 2400


def fnum(v):
    return float(v) if v not in (None, "") else None


def inum(v):
    return int(float(v)) if v not in (None, "") else None


# ---------- area and zoom ----------
gps = [(float(r["lat"]), float(r["lon"])) for r in records if r["lat"]]
finish_latlon = P.opt("course", "finish_latlon") or gps[-1]
bbox = P.opt("map", "bbox")
if not bbox:
    lats = [p[0] for p in gps] + [finish_latlon[0]]
    lons = [p[1] for p in gps] + [finish_latlon[1]]
    pad_lat, pad_lon = max(0.02, (max(lats) - min(lats)) * 0.08), max(0.02, (max(lons) - min(lons)) * 0.08)
    bbox = dict(n=max(lats) + pad_lat, s=min(lats) - pad_lat, w=min(lons) - pad_lon, e=max(lons) + pad_lon)
N, S, W, EAST = bbox["n"], bbox["s"], bbox["w"], bbox["e"]


def world_px(lat, lon, world):
    return ((lon + 180) / 360 * world, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * world)


Z = P.opt("map", "zoom")
if Z is None:
    Z = 8
    for z in range(8, 14):
        world = 256 * 2 ** z
        (xa, ya), (xb, yb) = world_px(N, W, world), world_px(S, EAST, world)
        if max(xb - xa, yb - ya) <= MAX_PX:
            Z = z
WORLD = 256 * 2 ** Z
x0, y0 = map(int, world_px(N, W, WORLD))
x1, y1 = map(int, world_px(S, EAST, WORLD))
W_PX, H_PX = x1 - x0, y1 - y0
ATTRIBUTION = "Relief (c) Esri | Labels (c) OpenStreetMap contributors (c) CARTO"


# ---------- basemap ----------
missing_tiles = []


def fetch(url, path):
    """A cached tile; a blank one (not cached) when it can't be downloaded, so an offline build still completes."""
    if not os.path.exists(path):
        try:
            if os.environ.get("RELAY_RECAP_OFFLINE"):
                raise OSError("RELAY_RECAP_OFFLINE is set")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            os.makedirs(TILES, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            time.sleep(0.05)
        except OSError as e:
            missing_tiles.append((url, e))
            return Image.new("RGBA", (256, 256), (0, 0, 0, 0)) if "labels" in url else Image.new("L", (256, 256), 180)
    return Image.open(path)


def mosaic(tile_px, z, url_fmt, name, mode):
    scale = WORLD / (tile_px * 2 ** z)  # map px per source-tile px
    canvas = Image.new(mode, (W_PX, H_PX))
    step = tile_px * scale
    for tx in range(int(x0 // step), int(x1 // step) + 1):
        for ty in range(int(y0 // step), int(y1 // step) + 1):
            im = fetch(url_fmt.format(z=z, x=tx, y=ty), os.path.join(TILES, f"{name}_{z}_{tx}_{ty}.png")).convert(mode)
            if im.size[0] != step:
                im = im.resize((int(step), int(step)), Image.LANCZOS)
            canvas.paste(im, (int(tx * step - x0), int(ty * step - y0)))
    return canvas


relief = mosaic(256, Z, "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
                "hillshade", "L")
themes = {"light": dict(black="#4C5A56", mid="#AEB9B3", white="#EEF1ED", labels="light_only_labels"),
          "dark": dict(black="#0A100F", mid="#1A2422", white="#3A4744", labels="dark_only_labels")}
basemaps = {}
for theme, t in themes.items():
    base = ImageOps.colorize(relief, black=t["black"], mid=t["mid"], white=t["white"], midpoint=135).convert("RGBA")
    labels = mosaic(512, Z - 1, "https://a.basemaps.cartocdn.com/" + t["labels"] + "/{z}/{x}/{y}@2x.png", t["labels"], "RGBA")
    base.alpha_composite(labels)
    basemaps[theme] = P.output(f"basemap_{theme}.webp")
    base.convert("RGB").save(basemaps[theme], "WEBP", quality=72, method=6)


def proj(lat, lon):
    x, y = world_px(lat, lon, WORLD)
    return [x - x0, y - y0]


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (xa, ya), (xb, yb) = pts[a], pts[b]
        dx, dy = xb - xa, yb - ya
        length = math.hypot(dx, dy)
        best, idx = 0.0, None
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - xa) - dx * (py - ya)) / length if length > 1e-6 else math.hypot(px - xa, py - ya)
            if d > best:
                best, idx = d, i
        if idx is not None and best > eps:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def midpoint(pts):
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    half, acc = sum(seg) / 2, 0.0
    for i, s in enumerate(seg):
        if acc + s >= half:
            f = (half - acc) / s if s else 0
            return [pts[i][0] + f * (pts[i + 1][0] - pts[i][0]), pts[i][1] + f * (pts[i + 1][1] - pts[i][1])]
        acc += s
    return pts[-1]


def r1(p):
    return [round(p[0], 1), round(p[1], 1)]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- legs ----------
legs = []
for n in sorted(roster):
    ro = roster[n]
    legs.append(dict(n=n, mi=float(ro["miles"]), rating=ro.get("rating", "") or "",
                     gain=inum(ro.get("gain_ft")), loss=-abs(inum(ro.get("loss_ft"))) if inum(ro.get("loss_ft")) is not None else None,
                     max_ft=inum(ro.get("max_ft")), min_ft=inum(ro.get("min_ft")),
                     from_name=ro.get("from_name") or None, to_name=ro.get("to_name") or None,
                     runner=ro["runner"], van=inum(ro.get("van")), plan_runner=ro.get("plan_runner") or ro["runner"],
                     hm_pace=ro.get("hm_pace", "") or "", surface=ro.get("surface", "") or "", note=ro.get("note", "") or ""))
N_LEGS = len(legs)

off = dict(E.get("official") or {})
off["team"] = E["team"]
off.setdefault("start", records[0]["timestamp_utc"].replace(" ", "T") + "Z")
off.setdefault("finish", records[-1]["timestamp_utc"].replace(" ", "T") + "Z")
T0 = utc_naive(off["start"])

recs = {}
for r in records:
    recs.setdefault(int(r["leg"]), []).append(r)


def profile_of(rows):
    out, last = [], None
    for r in rows:
        if not r["alt_m"]:
            continue
        ts = utc_naive(r["timestamp_utc"])
        if last is not None and (ts - last).total_seconds() < 60:
            continue
        out.append([round((ts - T0).total_seconds() / 60, 1), round(float(r["alt_m"]) * 3.28084)])
        last = ts
    return out


tracks = []
for n in sorted(manifest):
    row = manifest[n]
    runs = []
    for r in recs.get(n, []):
        recon = r["source"] == "reconstructed"
        if not runs or runs[-1][0] != recon:
            runs.append((recon, []))
        runs[-1][1].append(r)
    pieces = []
    for recon, rs in runs:
        pts = [proj(float(r["lat"]), float(r["lon"])) for r in rs if r["lat"]]
        if len(pts) >= 2:
            pieces.append(dict(recon=recon, path=[r1(p) for p in rdp(pts, 0.8)], profile=profile_of(rs)))
    if not pieces:
        continue
    recon_mi = float(row["reconstructed_miles"])
    rec_mi = round(float(row["miles"]) - recon_mi, 2)
    recorded = any(not p["recon"] for p in pieces)
    part_recon = recorded and any(p["recon"] for p in pieces)
    path = [pt for p in pieces for pt in p["path"]]
    official_mi = legs[n - 1]["mi"]
    key = f"T{n}"
    legs[n - 1]["key"] = key
    tracks.append(dict(key=key, kind="track", recon=not recorded, part_recon=part_recon,
                       recon_where=("first" if pieces[0]["recon"] else "last" if pieces[-1]["recon"] else "middle") if part_recon else None,
                       legs=[n], file="; ".join(x for x in (row["recorded_files"], row["reconstructed_files"]) if x),
                       start=iso(utc_naive(row["start_utc"])), end=iso(utc_naive(row["end_utc"])),
                       official_mi=official_mi, rec_mi=rec_mi, recon_mi=round(recon_mi, 2),
                       avg_hr=int(row["avg_hr"]) if row["avg_hr"] else None,
                       partial=recorded and rec_mi + recon_mi < 0.9 * official_mi,
                       pieces=pieces, mid=r1(midpoint(path)), first=path[0], last=path[-1]))

# ---------- legs without a track: one connector per run of missing legs ----------
finish_px = r1(proj(*finish_latlon))
segments, prev, n_gap = [], None, 0
for tr in tracks + [None]:
    expected = prev["legs"][-1] + 1 if prev else 1
    upto = tr["legs"][0] if tr else N_LEGS + 1
    missing = list(range(expected, upto))
    if missing:
        if prev is None:
            die("leg 1 has no track; the map needs a start point")
        n_gap += 1
        g = dict(key=f"G{n_gap}", kind="gap", legs=missing, start=prev["end"], end=tr["start"] if tr else off["finish"],
                 official_mi=round(sum(legs[k - 1]["mi"] for k in missing), 1), to=tr["first"] if tr else finish_px)
        g["from"] = prev["last"]
        g["mid"] = r1([(g["from"][0] + g["to"][0]) / 2, (g["from"][1] + g["to"][1]) / 2])
        for k in missing:
            legs[k - 1]["key"] = g["key"]
        segments.append(g)
    if tr:
        segments.append(tr)
    prev = tr

# ---------- view box: route + finish, padded, clamped to the basemap ----------
xs = [p[0] for t in tracks for pc in t["pieces"] for p in pc["path"]] + [finish_px[0]]
ys = [p[1] for t in tracks for pc in t["pieces"] for p in pc["path"]] + [finish_px[1]]
pad = 90
vx0, vy0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
vx1, vy1 = min(W_PX, max(xs) + pad), min(H_PX, max(ys) + pad)
view = [round(vx0), round(vy0), round(vx1 - vx0), round(vy1 - vy0)]

# ---------- runner summary ----------
track_of = {t["legs"][0]: t for t in tracks}
runners = {}
for L in legs:
    r = runners.setdefault(L["runner"], dict(name=L["runner"], van=L["van"], legs=[], mi=0.0, secs=0, recon_legs=[], hm_pace=L["hm_pace"]))
    r["legs"].append(L["n"])
    r["mi"] += L["mi"]
    t = track_of.get(L["n"])
    if t:
        r["secs"] += round((utc_naive(t["end"]) - utc_naive(t["start"])).total_seconds())
        if t["recon"] or t["part_recon"]:
            r["recon_legs"].append(L["n"])
for r in runners.values():
    r["mi"] = round(r["mi"], 1)
    r["legs_label"] = legs_label(r["legs"])
off.setdefault("runners", len(runners))

recorded_tracks = [t for t in tracks if not t["recon"]]
rebuilt = [t for t in tracks if t["recon"]]
files = {f for row in manifest.values() for f in row["recorded_files"].split("; ") if f}
recon_files = {f for row in manifest.values() for f in row["reconstructed_files"].split("; ") if f}
coverage = dict(course_mi=round(sum(l["mi"] for l in legs), 1), files=len(files), recon_files=len(recon_files),
                track_legs=len(recorded_tracks), recon_legs=len(rebuilt), gap_legs=N_LEGS - len(recorded_tracks) - len(rebuilt),
                track_official_mi=round(sum(t["official_mi"] for t in recorded_tracks), 1),
                recon_official_mi=round(sum(t["official_mi"] for t in rebuilt), 1),
                gap_official_mi=round(sum(s["official_mi"] for s in segments if s["kind"] == "gap"), 1),
                gps_mi=round(sum(t["rec_mi"] for t in tracks), 1),
                recon_leg_numbers=[n for t in rebuilt for n in t["legs"]],
                part_recon=[dict(legs=t["legs"], recon_mi=t["recon_mi"], where=t["recon_where"]) for t in recorded_tracks if t["part_recon"]],
                gap_leg_numbers=[n for s in segments if s["kind"] == "gap" for n in s["legs"]])
for t in tracks:
    del t["recon_where"]

# ---------- night: sunset to sunrise at the middle of the course ----------
mid_lat, mid_lon = gps[len(gps) // 2]
T1 = utc_naive(off["finish"])
nights = [dict(sunset=iso(a), sunrise=iso(b)) for a, b in nights_between(mid_lat, mid_lon, T0, T1)]

course = E.get("course") or {}
event = dict(name=E["event"], short=E.get("event_short") or E["event"], dates_label=E.get("dates_label", ""),
             team=E["team"], timezone=E["timezone"], tz_abbr=P.tz_abbr(T0), slug=P.slug, combined_fit=os.path.basename(P.output("combined.fit")),
             start_name=course.get("start_name") or f"Leg 1 start", finish_name=course.get("finish_name") or "the finish",
             start_short=course.get("start_short") or course.get("start_name") or "Start",
             finish_short=course.get("finish_short") or course.get("finish_name") or "Finish",
             start_label=course.get("start_label") or course.get("start_short") or course.get("start_name") or "",
             finish_label=course.get("finish_label") or course.get("finish_short") or course.get("finish_name") or "",
             dek=P.opt("map", "dek"), footer=P.opt("map", "footer", default=[]),
             has_van=any(L["van"] is not None for L in legs), has_rating=any(L["rating"] for L in legs),
             has_hm_pace=any(L["hm_pace"] for L in legs), has_climb=any(L["gain"] is not None for L in legs),
             has_plan=any(L["plan_runner"] != L["runner"] for L in legs))

data = dict(meta=dict(width=W_PX, height=H_PX, attribution=ATTRIBUTION), event=event,
            official=off, nights=nights, coverage=coverage, legs=legs, segments=segments, runners=list(runners.values()),
            view=view, start=tracks[0]["first"], finish=finish_px)
data_json = json.dumps(data, separators=(",", ":"))
open(P.output("map_data.json"), "w").write(data_json)


# ---------- assemble ----------
def data_uri(path):
    return "data:image/webp;base64," + base64.b64encode(open(path, "rb").read()).decode()


title = P.opt("map", "title") or f"{E['team']}'s {event['short']}"
desc = P.opt("map", "description") or (f"Team {E['team']}'s {E['event']}: the combined GPS route"
                                       f"{' from ' + course['start_short'] + ' to ' + course['finish_short'] if course.get('start_short') and course.get('finish_short') else ''}, leg by leg.")
page = open(os.path.join(HERE, "map_template.html"), encoding="utf-8").read()
page = (page.replace("__TITLE__", html.escape(title))
            .replace("__DESCRIPTION__", html.escape(desc))
            .replace("__DATA__", data_json.replace("</", "<\\/"))
            .replace("__BASEMAP_LIGHT__", data_uri(basemaps["light"]))
            .replace("__BASEMAP_DARK__", data_uri(basemaps["dark"])))
for token in ("__TITLE__", "__DESCRIPTION__", "__DATA__", "__BASEMAP_LIGHT__", "__BASEMAP_DARK__"):
    assert token not in page, token
out = P.output("route_map.html")
open(out, "w", encoding="utf-8").write(page)
size_mb = os.path.getsize(out) / 1e6
print(f"zoom {Z}, basemap {W_PX}x{H_PX} px, {len(os.listdir(TILES)) if os.path.isdir(TILES) else 0} tiles cached")
if missing_tiles:
    print(f"WARNING: {len(missing_tiles)} basemap tiles could not be downloaded and are blank "
          f"(first: {missing_tiles[0][1]}); rerun with network access for a complete basemap")
print("coverage:", coverage)
print("nights:", nights or "none during the race")
print(f"{out} {size_mb:.2f} MB" + ("  WARNING: over 16 MB, too large to publish as an artifact" if size_mb > 16 else ""))
