"""Export the scene data for the 3D flythrough: out/flythrough/scene.json.

Usage: python3 export_flythrough.py <project_dir> [first-last]   (default: every leg)
Inputs: event.json, roster.csv, out/<slug>_legs_manifest.csv, out/<slug>_merged_records.csv and, if present,
out/<slug>_places.json (geocode_places.py).

event.json "flythrough" options (all optional):
  imagery        "usgs" (US only, public domain, summer imagery), "esri" (worldwide), "eox" (Sentinel-2 cloudless,
                 worldwide, CC BY-NC-SA 4.0: non-commercial only), or a custom XYZ tile URL with {z}/{x}/{y}
  imagery_credit credit line when imagery is a custom URL
  block_size     legs per fixed camera pose (default 5)
  pitch          camera pitch in degrees (default 50)
  exaggeration   terrain exaggeration (default 1.8)
  base_s, per_mi_s   seconds per leg = base_s + per_mi_s * miles (defaults 3.0 and 0.35)
  width, height  video size in px (default 1080x1920, vertical)
Legs before the selected range are drawn as finished context.
"""
import json, math, os, sys
from datetime import datetime, timezone
from relaylib import Project, utc_naive, rebuilt_tag, legs_label, die

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args:
    sys.exit(__doc__)
P = Project(args[0])
roster, manifest = P.roster(), P.manifest()
if len(args) > 1:
    first, _, last = args[1].partition("-")
    SELECTED = list(range(int(first), int(last or first) + 1))
else:
    SELECTED = sorted(roster)
if not set(SELECTED) <= set(roster):
    die(f"legs {args[1]} are outside the roster (1-{len(roster)})")

IMAGERY = {
    "usgs": ("https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
             "Imagery: map services and data available from U.S. Geological Survey, National Geospatial Program"),
    "esri": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
             "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community"),
    "eox": ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2023_3857/default/g/{z}/{y}/{x}.jpg",
            "Imagery: Sentinel-2 cloudless 2023 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2023)"),
}
choice = P.opt("flythrough", "imagery", default="usgs")
if choice in IMAGERY:
    imagery_url, imagery_credit = IMAGERY[choice]
elif "{z}" in str(choice):
    imagery_url, imagery_credit = choice, P.opt("flythrough", "imagery_credit", default="Imagery: custom tiles")
else:
    die(f"flythrough.imagery must be one of {sorted(IMAGERY)} or a tile URL with {{z}}/{{x}}/{{y}}")
TERRAIN = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
has_recon = any(float(r["reconstructed_miles"]) > 0 for r in manifest.values())
CREDIT = " | ".join([imagery_credit, "Terrain: AWS Terrain Tiles (USGS 3DEP, SRTM and others)"]
                    + (["Reconstructed legs routed on (c) OpenStreetMap contributors"] if has_recon else []))
COLORS = P.fly_colors(roster)


def epoch(s):
    return utc_naive(s).replace(tzinfo=timezone.utc).timestamp()


def thin(points, min_m, min_s=None):
    out = [points[0]]
    for p in points[1:-1]:
        q = out[-1]
        d = math.hypot((p[1] - q[1]) * 110540, (p[2] - q[2]) * 111320 * math.cos(math.radians(p[1])))
        if d >= min_m or (min_s and p[0] - q[0] >= min_s):
            out.append(p)
    return out + points[-1:]


def lonlat(points):
    return [[round(p[2], 6), round(p[1], 6)] for p in points]


pts = {}
for r in P.merged_records():
    if r["lat"]:
        pts.setdefault(int(r["leg"]), []).append((epoch(r["timestamp_utc"]), float(r["lat"]), float(r["lon"])))

legs = []
for n in SELECTED:
    ro, row = roster[n], manifest[n]
    if n not in pts:
        die(f"leg {n} has no GPS points")
    split = float(row["duration_min"]) * 60 / float(ro["miles"])
    p = thin(pts[n], 8, 20)
    legs.append(dict(n=n, runner=ro["runner"], color=COLORS[ro["runner"]], van=int(ro["van"]) if (ro.get("van") or "").isdigit() else (ro.get("van") or ""), rating=ro.get("rating") or "",
                     miles=float(ro["miles"]), pace=f"{int(split // 60)}:{round(split % 60):02d}",
                     tag=rebuilt_tag(float(row["reconstructed_miles"]), float(row["miles"])),
                     coords=lonlat(p), times=[round(q[0]) for q in p]))
context = [dict(n=n, color=COLORS[roster[n]["runner"]], coords=lonlat(thin(pts[n], 20))) for n in sorted(pts) if n < SELECTED[0]]
course = lonlat(thin([q for n in sorted(pts) for q in pts[n]], 30))
runners = []
for name in P.runner_order(roster):
    ns = [n for n in sorted(roster) if roster[n]["runner"] == name]
    runners.append(dict(name=name, color=COLORS[name], legs=legs_label(ns)))

places, states = [], []
places_path = P.output("places.json")
if os.path.exists(places_path):
    pl = json.load(open(places_path))
    places = [dict(name=p["name"], lon=p["lon"], lat=p["lat"], tier=p["tier"], kind=p["kind"]) for p in pl["places"]]
    short = lambda name, code: code.split("-")[-1] if code.startswith("US-") else name
    for ln in pl.get("region_lines", []):
        places.append(dict(name=f"{short(ln['from_state'], ln['from_code'])} | {short(ln['to_state'], ln['to_code'])} "
                                f"{'state line' if ln['to_code'].startswith('US-') else 'border'}",
                           lon=round(ln["lon"], 6), lat=round(ln["lat"], 6), tier=1, kind="border"))
    # the intro and outro wide shots are too zoomed out for towns; they get the region names instead
    states = [dict(name=name.upper(), lon=v["lon"], lat=v["lat"], tier=0, kind="state") for name, v in pl["states"].items()]
else:
    print(f"no {os.path.basename(places_path)}: run geocode_places.py for town and region labels")

# time zone abbreviations as Python knows them ("CEST"; browsers often say "GMT+2"), switching at DST changes
tz_abbrs, last = [], None
for L in legs:
    for t in (L["times"][0], L["times"][-1]):
        abbr = P.tz_abbr(datetime.fromtimestamp(t, timezone.utc).replace(tzinfo=None))
        if abbr != last:
            tz_abbrs.append([t, abbr])
            last = abbr
options = dict(block_size=5, pitch=50, exaggeration=1.8, base_s=3.0, per_mi_s=0.35, width=1080, height=1920)
options.update({k: v for k, v in (P.opt("flythrough", default={}) or {}).items() if k in options})
work = os.path.join(P.out, "flythrough")
os.makedirs(work, exist_ok=True)
out = os.path.join(work, "scene.json")
json.dump(dict(selected=SELECTED, legs=legs, context=context, course=course, runners=runners, places=places, states=states,
               tiles=dict(imagery=imagery_url, terrain=TERRAIN), credit=CREDIT, team=P.event["team"].upper(),
               event=P.event["event"], timezone=P.event["timezone"], tz_abbrs=tz_abbrs, options=options),
          open(out, "w"), separators=(",", ":"))
print(f"{out}: legs {SELECTED[0]}-{SELECTED[-1]} ({sum(len(L['coords']) for L in legs)} points), "
      f"{len(context)} context legs, course {len(course)} points, {len(places)} places, "
      f"{len(states)} region labels, {os.path.getsize(out) // 1024} KB")
