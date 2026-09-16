"""Combine relay recordings (FIT and GPX) into one activity with one lap per leg.

Usage: python3 build_combined.py <project_dir>
Inputs: event.json, roster.csv, recordings.csv (file, legs [, start_utc, end_utc]) plus reconstructions listed in
gaps.csv, source files.
Outputs in out/:
  <slug>_combined.fit         one session, one lap per leg, heart rate kept (the full team record)
  <slug>_combined_strava.fit  the same without any heart rate (the file to upload to Strava)
  <slug>_combined.gpx         one named track per leg, "Leg N | Runner"
  <slug>_legs_manifest.csv    one row per leg
  <slug>_merged_records.csv   every record tagged with leg, runner and source (fit, gpx, reconstructed)

Rules:
- Recordings are ordered by time. A handoff overlap is cut from one side only: recorded data wins over a
  reconstruction; a watch started early and still standing at the exchange loses its head; otherwise the previous
  watch ran past the handoff and loses its tail.
- A recording that covers several legs is split at the exchanges, placed by the legs' official miles.
- start_utc / end_utc in recordings.csv drop a recording's records before / after that time: a teammate who ran along
  on a leg another watch already covers, or a watch left running in the van after the leg.
- Distance is continuous across the relay; timer stop/start events mark each exchange.
- A GPX whose creator/desc says "reconstructed" is kept and flagged (source "reconstructed").
- Activity type is FIT sport "generic" (Other) by default: a team relay is not one athlete's run.
"""
import csv, hashlib, os, sys
from xml.sax.saxutils import escape
from relaylib import (Project, FitWriter, ENUM, UINT8, UINT16, SINT32, UINT32, UINT32Z, M_PER_MI, fit_ts, scaled,
                      haversine_semi, load_any, SEMI, die)

if len(sys.argv) != 2:
    sys.exit(__doc__)
P = Project(sys.argv[1])
STANDING_M = P.opt("combine", "standing_m", default=50)  # an early-started watch that moved less is still waiting
SPORTS = {"generic": 0, "running": 1, "cycling": 2, "walking": 11, "hiking": 17}
SPORT_NAME = P.opt("combine", "sport", default="generic")
if SPORT_NAME not in SPORTS:
    die(f"combine.sport must be one of {sorted(SPORTS)}")
SPORT = SPORTS[SPORT_NAME]
GPX_TYPE = "other" if SPORT_NAME == "generic" else SPORT_NAME

roster = P.roster()
recordings = P.recordings()
covered = sorted({n for _, legs, _, _ in recordings for n in legs})
if covered != sorted(roster):
    die(f"recordings cover legs {covered}, roster.csv has {sorted(roster)}; every leg needs a recording or a "
        "reconstruction (gaps.csv)")
listed = {name for name, _, _, _ in recordings}
if os.path.isdir(P.sources):
    for name in sorted(os.listdir(P.sources)):
        if (".fit" in name.lower() or ".gpx" in name.lower()) and name not in listed:
            print(f"not in recordings.csv, skipped: {name}")

# ---------- load ----------
items, digests, window_notes = [], {}, {}
for name, legs, start_utc, end_utc in recordings:
    path = P.resolve(name)
    digest = hashlib.md5(open(path, "rb").read()).hexdigest()
    if digest in digests:
        die(f"{name} is a byte-identical copy of {digests[digest]}; list only one of them")
    digests[digest] = name
    recs, source = load_any(path)
    recs.sort(key=lambda r: r["ts"])
    if start_utc:  # the watch ran along on an earlier leg that another runner's recording covers
        recs = [r for r in recs if r["ts"] >= start_utc]
        for n in legs:
            window_notes[n] = f"{name} from {start_utc} UTC; its earlier part ran along on the previous leg"
    if end_utc:  # the watch kept running after the leg
        recs = [r for r in recs if r["ts"] <= end_utc]
        for n in legs:
            window_notes[n] = "; ".join(filter(None, [window_notes.get(n), f"{name} until {end_utc} UTC"]))
    if not recs:
        die(f"{name} has no timestamped records" + (" inside its start_utc/end_utc window" if start_utc or end_utc else ""))
    # some exports (Strava) log several samples per second; collapse each second, latest non-null value wins
    collapsed = []
    for r in recs:
        if collapsed and collapsed[-1]["ts"] == r["ts"]:
            collapsed[-1].update({k: v for k, v in r.items() if v is not None})
        else:
            collapsed.append(r)
    for r in collapsed:
        r["source"], r["file"] = source, name
    items.append({"file": name, "legs": legs, "source": source, "recs": collapsed,
                  "start": collapsed[0]["ts"], "end": collapsed[-1]["ts"], "trimmed_s": 0})
items.sort(key=lambda it: it["start"])

order = [n for it in items for n in it["legs"]]
if order != sorted(order):
    die(f"recordings sorted by start time give leg order {order}; check recordings.csv leg numbers and watch clocks")


# ---------- trim handoff overlaps: the overlap is cut from one side only ----------
def moved_m(recs, until):
    pts = [r for r in recs if r["lat"] is not None and r["ts"] <= until]
    return haversine_semi((pts[0]["lat"], pts[0]["lon"]), (pts[-1]["lat"], pts[-1]["lon"])) if pts else 0.0


for prev, nxt in zip(items, items[1:]):
    if prev["end"] <= nxt["start"]:  # a shared handoff second is dropped once by the merge below
        continue
    overlap = (prev["end"] - nxt["start"]).total_seconds()
    prev_recon, next_recon = prev["source"] == "reconstructed", nxt["source"] == "reconstructed"
    if prev_recon != next_recon:
        cut_next = next_recon  # keep the recorded side over the reconstruction
    else:
        cut_next = moved_m(nxt["recs"], prev["end"]) < STANDING_M
    if cut_next:
        keep = [r for r in nxt["recs"] if r["ts"] > prev["end"]]
        if not keep:
            die(f"{nxt['file']} lies entirely inside {prev['file']}; check recordings.csv")
        nxt["trimmed_s"] += (keep[0]["ts"] - nxt["start"]).total_seconds()
        nxt["recs"], nxt["start"] = keep, keep[0]["ts"]
    else:
        keep = [r for r in prev["recs"] if r["ts"] < nxt["start"]]
        if not keep:
            die(f"{prev['file']} lies entirely after the start of {nxt['file']}; check recordings.csv")
        prev["trimmed_s"] += (prev["end"] - keep[-1]["ts"]).total_seconds()
        prev["recs"], prev["end"] = keep, keep[-1]["ts"]
    print(f"overlap {overlap:.0f}s: trimmed {'start of ' + nxt['file'] if cut_next else 'end of ' + prev['file']}")

# ---------- merge with continuous distance; split multi-leg recordings at the exchange ----------
merged, offset, split_notes = [], 0.0, {}
for it in items:
    d0 = next((r["dist"] for r in it["recs"] if r["dist"] is not None), 0.0)
    local, kept = 0.0, []
    for r in it["recs"]:
        if merged and r["ts"] <= merged[-1]["ts"]:
            continue
        if r["dist"] is not None:
            local = max(local, r["dist"] - d0)
        r["local"], r["cum"] = local, offset + local
        merged.append(r)
        kept.append(r)
    miles = [float(roster[n]["miles"]) for n in it["legs"]]
    cuts = [local * sum(miles[:k + 1]) / sum(miles) for k in range(len(miles) - 1)]
    for r in kept:
        r["leg"] = it["legs"][sum(r["local"] >= c for c in cuts)]
    if cuts:
        edges = [0.0] + cuts + [local]
        for k, n in enumerate(it["legs"]):
            split_notes[n] = (f"{it['file']} from {edges[k] / M_PER_MI:.2f} to {edges[k + 1] / M_PER_MI:.2f} mi, "
                              "split by official leg miles")
    offset += local

by_leg = {}
for r in merged:
    by_leg.setdefault(r["leg"], []).append(r)
missing = [n for n in roster if n not in by_leg]
if missing:
    die(f"no records left for legs {missing} after trimming overlaps")
legs = []
for n in sorted(roster):
    pts = by_leg[n]
    hrs = [r["hr"] for r in pts if r["hr"]]
    legs.append(dict(
        n=n, runner=roster[n]["runner"], van=roster[n].get("van", ""), pts=pts, start=pts[0]["ts"], end=pts[-1]["ts"],
        dist_m=pts[-1]["cum"] - pts[0]["cum"],
        recon_m=sum(b["cum"] - a["cum"] for a, b in zip(pts, pts[1:]) if b["source"] == "reconstructed" and a["file"] == b["file"]),
        recorded_files=list(dict.fromkeys(r["file"] for r in pts if r["source"] != "reconstructed")),
        reconstructed_files=list(dict.fromkeys(r["file"] for r in pts if r["source"] == "reconstructed")),
        avg_hr=round(sum(hrs) / len(hrs)) if hrs else None, max_hr=max(hrs) if hrs else None,
        note="; ".join(filter(None, [window_notes.get(n), split_notes.get(n)]))))

# ---------- manifest ----------
with open(P.output("legs_manifest.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["leg", "runner", "van", "start_utc", "end_utc", "duration_min", "miles", "reconstructed_miles",
                "recorded_files", "reconstructed_files", "gap_to_next_min", "handoff_dist_to_next_m", "avg_hr", "max_hr", "note"])
    for i, L in enumerate(legs):
        gap = hand = ""
        if i + 1 < len(legs):
            nxt = legs[i + 1]
            gap = round((nxt["start"] - L["end"]).total_seconds() / 60, 1)
            a = next((r for r in reversed(L["pts"]) if r["lat"] is not None), None)
            b = next((r for r in nxt["pts"] if r["lat"] is not None), None)
            hand = round(haversine_semi((a["lat"], a["lon"]), (b["lat"], b["lon"]))) if a and b else ""
        w.writerow([L["n"], L["runner"], L["van"], L["start"], L["end"],
                    round((L["end"] - L["start"]).total_seconds() / 60, 1), round(L["dist_m"] / M_PER_MI, 2),
                    round(L["recon_m"] / M_PER_MI, 2), "; ".join(L["recorded_files"]), "; ".join(L["reconstructed_files"]),
                    gap, hand, L["avg_hr"], L["max_hr"], L["note"]])

with open(P.output("merged_records.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp_utc", "leg", "runner", "source", "lat", "lon", "alt_m", "speed_mps", "hr", "cadence", "power", "distance_m"])
    for r in merged:
        w.writerow([r["ts"], r["leg"], roster[r["leg"]]["runner"], r["source"],
                    r["lat"] * SEMI if r["lat"] is not None else "", r["lon"] * SEMI if r["lon"] is not None else "",
                    r["alt"], r["spd"], r["hr"], r["cad"], r["pwr"], round(r["cum"], 2)])

# ---------- GPX: one named track per leg ----------
recon_files = sorted({r["file"] for r in merged if r["source"] == "reconstructed"})
with open(P.output("combined.gpx"), "w", encoding="ascii", errors="xmlcharrefreplace") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="build_combined.py" '
            'xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">\n'
            f"<metadata><name>{escape(P.event['event'] + ' - ' + P.event['team'])}</name>")
    if recon_files:
        f.write(f"<desc>{escape('Reconstructed from the official course and handoff times, not recorded GPS: ' + ', '.join(recon_files))}</desc>")
    f.write("</metadata>\n")
    for L in legs:
        desc = f"{roster[L['n']]['miles']} mi official, {L['dist_m'] / M_PER_MI:.2f} mi tracked"
        if L["recon_m"] > 0:
            desc += f", {L['recon_m'] / M_PER_MI:.2f} mi reconstructed (not recorded GPS)"
        if L["note"]:
            desc += f"; {L['note']}"
        name = f"Leg {L['n']} | {L['runner']}"
        f.write(f"<trk><name>{escape(name)}</name><desc>{escape(desc)}</desc><type>{GPX_TYPE}</type>\n<trkseg>\n")
        for r in L["pts"]:
            if r["lat"] is None:
                continue
            f.write(f'<trkpt lat="{r["lat"]*SEMI:.7f}" lon="{r["lon"]*SEMI:.7f}">')
            if r["alt"] is not None:
                f.write(f"<ele>{r['alt']:.1f}</ele>")
            f.write(f"<time>{r['ts'].strftime('%Y-%m-%dT%H:%M:%SZ')}</time>")
            if r["hr"]:
                f.write(f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{r['hr']}</gpxtpx:hr>"
                        f"</gpxtpx:TrackPointExtension></extensions>")
            f.write("</trkpt>\n")
        f.write("</trkseg></trk>\n")
    f.write("</gpx>\n")


# ---------- FIT ----------
def write_fit(path, heart_rate=True):
    """One session with one lap per leg. heart_rate=False leaves every heart-rate field out of the file."""
    fw = FitWriter()
    t0, t_end = merged[0]["ts"], merged[-1]["ts"]
    hr_record = [(3, UINT8)] if heart_rate else []
    hr_lap = [(15, UINT8), (16, UINT8)] if heart_rate else []
    hr_session = [(16, UINT8), (17, UINT8)] if heart_rate else []

    fw.define(0, 0, [(0, ENUM), (1, UINT16), (2, UINT16), (3, UINT32Z), (4, UINT32)])  # file_id (first message)
    fw.write(0, {0: 4, 1: 255, 2: 0, 3: int(P.local(t0).strftime("%Y%m%d")), 4: fit_ts(t0)})

    fw.define(1, 21, [(253, UINT32), (0, ENUM), (1, ENUM), (4, UINT8)])  # event
    fw.define(2, 20, [(253, UINT32), (0, SINT32), (1, SINT32), (78, UINT32), (73, UINT32),
                      *hr_record, (4, UINT8), (7, UINT16), (5, UINT32)])  # record
    fw.define(3, 19, [(253, UINT32), (2, UINT32), (3, SINT32), (4, SINT32), (5, SINT32), (6, SINT32),
                      (7, UINT32), (8, UINT32), (9, UINT32), *hr_lap,
                      (254, UINT16), (0, ENUM), (1, ENUM), (25, ENUM)])  # lap

    timer_total = 0.0
    for i, L in enumerate(legs):
        pts = L["pts"]
        fw.write(21, {253: fit_ts(pts[0]["ts"]), 0: 0, 1: 0, 4: 0})  # timer start at the exchange
        for r in pts:
            fw.write(20, {253: fit_ts(r["ts"]), 0: r["lat"], 1: r["lon"], 78: scaled(r["alt"], 5, 500),
                          73: scaled(r["spd"], 1000), 3: r["hr"], 4: r["cad"], 7: r["pwr"], 5: scaled(r["cum"], 100)})
        fw.write(21, {253: fit_ts(pts[-1]["ts"]), 0: 0, 1: 4 if i == len(legs) - 1 else 1, 4: 0})  # timer stop
        gps = [r for r in pts if r["lat"] is not None] or [{"lat": None, "lon": None}]
        dur = (pts[-1]["ts"] - pts[0]["ts"]).total_seconds()
        timer_total += dur
        fw.write(19, {253: fit_ts(pts[-1]["ts"]), 2: fit_ts(pts[0]["ts"]),
                      3: gps[0]["lat"], 4: gps[0]["lon"], 5: gps[-1]["lat"], 6: gps[-1]["lon"],
                      7: dur * 1000, 8: dur * 1000, 9: L["dist_m"] * 100,
                      15: L["avg_hr"], 16: L["max_hr"], 254: i, 0: 9, 1: 1, 25: SPORT})

    all_hr = [r["hr"] for r in merged if r["hr"]]
    elapsed = (t_end - t0).total_seconds()
    gps0 = next(r for r in merged if r["lat"] is not None)
    fw.define(4, 18, [(253, UINT32), (2, UINT32), (3, SINT32), (4, SINT32), (5, ENUM), (6, ENUM),
                      (7, UINT32), (8, UINT32), (9, UINT32), *hr_session,
                      (25, UINT16), (26, UINT16), (254, UINT16), (0, ENUM), (1, ENUM)])  # session
    fw.write(18, {253: fit_ts(t_end), 2: fit_ts(t0), 3: gps0["lat"], 4: gps0["lon"], 5: SPORT, 6: 0,
                  7: elapsed * 1000, 8: timer_total * 1000, 9: offset * 100,
                  16: round(sum(all_hr) / len(all_hr)) if all_hr else None, 17: max(all_hr) if all_hr else None,
                  25: 0, 26: len(legs), 254: 0, 0: 8, 1: 1})
    utc_offset = P.local(t_end).utcoffset().total_seconds()
    fw.define(5, 34, [(253, UINT32), (0, UINT32), (1, UINT16), (2, ENUM), (3, ENUM), (4, ENUM), (5, UINT32)])  # activity
    fw.write(34, {253: fit_ts(t_end), 0: timer_total * 1000, 1: 1, 2: 0, 3: 26, 4: 1, 5: fit_ts(t_end) + utc_offset})

    with open(path, "wb") as f:
        f.write(fw.bytes())
    return elapsed, timer_total


elapsed, timer_total = write_fit(P.output("combined.fit"))
# the upload copy for Strava: heart rate from many people would be read as one athlete's effort
write_fit(P.output("combined_strava.fit"), heart_rate=False)

for n, note in sorted(split_notes.items()):
    print(f"leg {n}: {note}")
print(f"{len(legs)} legs as laps from {len(items)} recordings ({len(recon_files)} reconstructed), {len(merged)} records, "
      f"{offset / M_PER_MI:.2f} mi, elapsed {elapsed / 3600:.2f} h, timer {timer_total / 3600:.2f} h -> {P.out}")
