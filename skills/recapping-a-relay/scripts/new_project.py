"""Start a relay recap project and list the source recordings, so each file can be matched to its legs.

Usage: python3 new_project.py <project_dir> --sources <folder of FIT/GPX files> [--slug NAME] [--timezone Area/City]
Creates (never overwrites) event.json, roster.csv, recordings.csv, gaps.csv and places.csv templates, then prints
every source file in start-time order: start/end in local time, duration, distance, start and end position, whether
it has heart rate, whether its creator says it is reconstructed, and byte-identical duplicates ("copy" files).
recordings.csv is pre-filled with every non-duplicate file and a blank legs column to fill in.
"""
import argparse, csv, hashlib, json, os, sys
from datetime import timezone
from zoneinfo import ZoneInfo
from relaylib import load_any, haversine_semi, SEMI, M_PER_MI

ap = argparse.ArgumentParser()
ap.add_argument("project")
ap.add_argument("--sources", required=True)
ap.add_argument("--slug", default="RELAY")
ap.add_argument("--timezone", default="America/New_York")
a = ap.parse_args()
root, sources = os.path.abspath(a.project), os.path.abspath(a.sources)
if not os.path.isdir(sources):
    sys.exit(f"error: {sources} is not a folder")
os.makedirs(root, exist_ok=True)
tz = ZoneInfo(a.timezone)

# ---------- inventory ----------
files, seen = [], {}
for name in sorted(os.listdir(sources)):
    low = name.lower()
    if not (low.endswith(".fit") or low.endswith(".gpx")):
        if ".fit" in low or ".gpx" in low:
            print(f"note: '{name}' looks like a Finder copy (extension not at the end); it is only checked for being a duplicate")
        else:
            continue
    path = os.path.join(sources, name)
    digest = hashlib.md5(open(path, "rb").read()).hexdigest()
    dup_of = seen.get(digest)
    seen.setdefault(digest, name)
    try:
        recs, source = load_any(path) if (low.endswith(".fit") or low.endswith(".gpx")) else (None, None)
    except Exception as e:
        print(f"could not read {name}: {e}")
        continue
    if recs is None:
        files.append(dict(name=name, dup_of=dup_of, bad_ext=True))
        continue
    recs = sorted(recs, key=lambda r: r["ts"])
    gps = [r for r in recs if r["lat"] is not None]
    dist = max((r["dist"] or 0) for r in recs) if recs else 0
    if not dist and len(gps) > 1:
        dist = sum(haversine_semi((p["lat"], p["lon"]), (q["lat"], q["lon"])) for p, q in zip(gps, gps[1:]))
    files.append(dict(name=name, dup_of=dup_of, source=source, start=recs[0]["ts"] if recs else None,
                      end=recs[-1]["ts"] if recs else None, mi=dist / M_PER_MI, hr=any(r["hr"] for r in recs),
                      first=(gps[0]["lat"] * SEMI, gps[0]["lon"] * SEMI) if gps else None,
                      last=(gps[-1]["lat"] * SEMI, gps[-1]["lon"] * SEMI) if gps else None))

usable = sorted((f for f in files if not f.get("dup_of") and not f.get("bad_ext") and f.get("start")), key=lambda f: f["start"])
local = lambda t: t.replace(tzinfo=timezone.utc).astimezone(tz)
print(f"\n{'#':>3}  {'start':<16} {'end':<6} {'dur':>6} {'mi':>6}  hr  kind           file")
for i, f in enumerate(usable, 1):
    dur = (f["end"] - f["start"]).total_seconds() / 60
    print(f"{i:>3}  {local(f['start']):%a %m-%d %H:%M} {local(f['end']):%H:%M} {int(dur // 60)}:{int(dur % 60):02d}"
          f" {f['mi']:>6.2f}  {'y ' if f['hr'] else '- '}  {f['source']:<14} {f['name']}")
for prev, nxt in zip(usable, usable[1:]):
    if nxt["start"] < prev["end"]:
        secs = (min(prev["end"], nxt["end"]) - nxt["start"]).total_seconds()
        hint = ("  <- long: two watches on the same leg (a teammate ran along?) - use start_utc/end_utc" if secs > 600
                else "  (handoff overlap, trimmed automatically)")
        print(f"     overlap {secs:.0f} s: {prev['name']} / {nxt['name']}{hint}")
for f in files:
    if f.get("dup_of"):
        print(f"duplicate (identical bytes), leave out: {f['name']} = {f['dup_of']}")
    elif f.get("bad_ext"):
        print(f"not a .fit/.gpx name, leave out or rename: {f['name']}")

# ---------- templates (never overwrite) ----------
def write_once(name, text):
    path = os.path.join(root, name)
    if os.path.exists(path):
        print(f"kept existing {name}")
        return
    open(path, "w", encoding="utf-8").write(text)
    print(f"wrote {name}")


event = {
    "slug": a.slug,
    "event": "Relay Name 2026",
    "event_short": "Relay Name",
    "dates_label": "Month D-D, 2026",
    "team": "Team Name",
    "timezone": a.timezone,
    "sources": os.path.relpath(sources, root),
    "output": "out",
    "course": {"summary": "Start Town to Finish Town", "start_name": "", "start_short": "", "finish_name": "",
               "finish_short": "", "finish_latlon": None},
    "official": {"run_time": "", "pace": "", "overall_place": None, "category": "", "category_place": None,
                 "bib": None, "city": "", "start": "", "finish": "", "source": ""},
    "combine": {"sport": "generic"},
    "reconstruct": {"elevation": "usgs"},
    "map": {"footer": []},
    "flythrough": {"imagery": "usgs"},
}
write_once("event.json", json.dumps(event, indent=2) + "\n")
write_once("roster.csv", "leg,runner,miles,van,rating,hm_pace,from_name,to_name,gain_ft,loss_ft,plan_runner,note\n")
write_once("recordings.csv", "file,legs,start_utc,end_utc\n" + "".join(f"{f['name']},,,\n" for f in usable))
write_once("gaps.csv", "id,legs,before,after,out,title,course,via\n")
write_once("places.csv", "name,query,tier,kind,lat,lon\n")
print(f"\nNext: fill event.json and roster.csv, put leg numbers in recordings.csv, then run build_combined.py {root}")
