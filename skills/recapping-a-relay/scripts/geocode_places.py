"""Geocode the place labels for the 3D flythrough: places.csv -> out/<slug>_places.json.

Usage: python3 geocode_places.py <project_dir> [--refresh]
places.csv columns: name, query, tier (1 = always worth showing, 3 = only if there is room), kind (town, mountain,
park, river, road, ...), and optional lat, lon. A row with its own lat/lon is used as-is: the escape hatch for names
Nominatim resolves to the wrong feature (a town instead of the summit of the same name). No places.csv is fine: the
video then shows only region names.

Every place is measured against the real course and dropped if it is more than flythrough.places_max_mi (default
14) from it; a wrong hit (there are many Jeffersons) lands far away and disappears here rather than in the video.
Region labels (states/provinces) come from reverse geocoding samples along the course; boundaries are found by
bisection. Nominatim allows one request a second, so the first run takes a minute or two; every lookup is cached in
<project>/cache/geocode.json and reruns are instant (--refresh ignores the cache).
"""
import csv, json, math, os, sys, time, urllib.parse, urllib.request
from relaylib import Project

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args:
    sys.exit(__doc__)
P = Project(args[0])
REFRESH = "--refresh" in sys.argv
CACHE = os.path.join(P.cache, "geocode.json")
UA = P.opt("reconstruct", "user_agent", default="relay-recap/1.0 (personal relay recap)")
MAX_MI = P.opt("flythrough", "places_max_mi", default=14.0)
SAMPLES = 40  # reverse-geocode samples along the course before bisecting region boundaries
PAUSE = 1.1

cache = json.load(open(CACHE)) if os.path.exists(CACHE) and not REFRESH else {}
lookups, reverse = cache.get("lookups", {}), cache.get("reverse", {})
queried = 0


def save_cache():
    json.dump(dict(lookups=lookups, reverse=reverse), open(CACHE, "w"), indent=1)


def get(url):
    global queried
    queried += 1
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    time.sleep(PAUSE)
    return out


def geocode(query):
    hits = get("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(dict(q=query, format="json", limit=1)))
    return [float(hits[0]["lon"]), float(hits[0]["lat"]), hits[0]["display_name"]] if hits else None


def region_of(lon, lat):
    key = f"{lat:.5f},{lon:.5f}"
    if key not in reverse:
        d = get("https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(dict(lon=lon, lat=lat, format="json", zoom=5)))
        a = d.get("address", {})
        reverse[key] = dict(region=a.get("state") or a.get("province") or a.get("region") or a.get("country") or "",
                            code=a.get("ISO3166-2-lvl4", ""))
        save_cache()
    return reverse[key]


def miles(a, b):
    return math.hypot((a[1] - b[1]) * 69.0, (a[0] - b[0]) * 69.0 * math.cos(math.radians(a[1])))


course = []
for r in P.merged_records():
    if r["lat"]:
        c = (float(r["lon"]), float(r["lat"]))
        if not course or miles(c, course[-1]) > 0.15:  # ~800 ft is plenty for a nearest-town test
            course.append(c)

# ---------- places ----------
places, dropped = [], []
path = P.file("places.csv")
rows = list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []
for row in rows:
    if row.get("lat") and row.get("lon"):
        hit = [float(row["lon"]), float(row["lat"]), row["query"] + " [pinned]"]
    elif row["name"] in lookups and lookups[row["name"]]["query"] == row["query"]:
        hit = lookups[row["name"]]["hit"]
    else:
        hit = geocode(row["query"])
        lookups[row["name"]] = dict(query=row["query"], hit=hit)
        save_cache()
    if not hit:
        dropped.append((row["name"], "no geocoding result", None))
        continue
    lon, lat, display = hit
    d = min(miles((lon, lat), c) for c in course)
    if d > MAX_MI:
        dropped.append((row["name"], f"{d:.1f} mi off course", display))
        continue
    places.append(dict(name=row["name"], query=row["query"], tier=int(row.get("tier") or 2), kind=row.get("kind") or "town",
                       lon=round(lon, 6), lat=round(lat, 6), display_name=display, dist_mi=round(d, 2)))

# ---------- regions along the course ----------
idx = sorted(set([round(k * (len(course) - 1) / SAMPLES) for k in range(SAMPLES + 1)]))
lines = []
for lo, hi in zip(idx, idx[1:]):
    r_lo, r_hi = region_of(*course[lo])["region"], region_of(*course[hi])["region"]
    if r_lo == r_hi:
        continue
    while hi - lo > 1:  # bisect to the boundary
        mid = (lo + hi) // 2
        if region_of(*course[mid])["region"] == r_lo:
            lo = mid
        else:
            hi = mid
    lines.append(dict(index=hi, lon=course[hi][0], lat=course[hi][1], from_state=r_lo, to_state=r_hi,
                      from_code=region_of(*course[lo])["code"], to_code=region_of(*course[hi])["code"]))

# anchors for the intro and outro wide shots: a region name over the middle of that region's share of the course
bounds = [0] + [ln["index"] for ln in lines] + [len(course)]
states = {}
for a, b in zip(bounds, bounds[1:]):
    seg = course[a:b]
    name = region_of(*seg[0])["region"] if seg else ""
    if seg and name and name not in states:
        mid = seg[len(seg) // 2]
        states[name] = dict(lon=round(mid[0], 6), lat=round(mid[1], 6), code=region_of(*seg[0])["code"])

save_cache()
out = P.output("places.json")
json.dump(dict(places=sorted(places, key=lambda p: (p["tier"], p["dist_mi"])), region_lines=lines, states=states),
          open(out, "w"), indent=1)

print(f"{len(places)} places kept, {len(dropped)} dropped, {queried} Nominatim requests\n")
print(f"{'place':<30}{'tier':>5}{'mi off course':>15}  nearest match")
for p in sorted(places, key=lambda p: p["dist_mi"]):
    print(f"{p['name']:<30}{p['tier']:>5}{p['dist_mi']:>15.2f}  {p['display_name'][:60]}")
for name, why, display in dropped:
    print(f"  DROPPED {name}: {why}" + (f" -> {display[:60]}" if display else ""))
for ln in lines:
    print(f"\nregion line: {ln['from_state']} -> {ln['to_state']} at {ln['lat']:.5f}, {ln['lon']:.5f}")
print("region anchors: " + ", ".join(f"{k} ({v['lat']:.3f}, {v['lon']:.3f})" for k, v in states.items()))
print(out)
