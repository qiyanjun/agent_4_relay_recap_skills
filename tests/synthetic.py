"""Build a small made-up relay project that exercises every combining rule, with no real data and no network.

make_project(dir) writes sources and config for a 6-leg relay in the Alps (Europe/Berlin), 3 runners:
  leg 1  A  A1.fit       keeps running 40 s past the handoff while B is already moving -> A's tail is trimmed
  leg 2-3 B B23.fit      one recording for two legs (1.0 + 2.0 mi) -> split at one third of its distance
  leg 4  C  C4.fit       started 60 s early, standing at the exchange -> C's head is trimmed
  leg 5  (gap)           rebuilt by reconstruct_gaps.py from cached route and elevation (gaps.csv)
  leg 6  A  A6.gpx       Strava-style GPX, two samples per second, heart rate in extensions; the first 5 minutes
                         ran along with C on leg 4 -> start_utc in recordings.csv drops them
Also a byte-identical "C4.fit copy" that must be skipped.
"""
import json, math, os, shutil, sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skills", "recapping-a-relay", "scripts"))
from relaylib import FitWriter, ENUM, UINT8, UINT16, SINT32, UINT32, UINT32Z, fit_ts, SEMI, M_PER_MI  # noqa: E402

LAT0, LON0 = 47.40, 11.00
T0 = datetime(2026, 7, 4, 6, 0, 0)  # naive UTC; 08:00 CEST
SPEED = 3.0  # m/s
M_PER_DEG_LAT = 111_320.0


def point_at(d_m):
    """Course heads north-east; d_m metres from the start."""
    return LAT0 + d_m * 0.7071 / M_PER_DEG_LAT, LON0 + d_m * 0.7071 / (M_PER_DEG_LAT * math.cos(math.radians(LAT0)))


def samples(t_start, d_start, seconds, speed=SPEED, hr=150, standing=0):
    """(ts, lat, lon, alt, hr, cum distance from d_start) once a second; `standing` seconds without moving first."""
    out = []
    for s in range(seconds + 1):
        moved = max(0, s - standing) * speed
        lat, lon = point_at(d_start + moved)
        out.append((t_start + timedelta(seconds=s), lat, lon, 600 + (d_start + moved) * 0.02, hr + (s % 7), moved))
    return out


def write_fit(path, pts):
    fw = FitWriter()
    fw.define(0, 0, [(0, ENUM), (1, UINT16), (2, UINT16), (3, UINT32Z), (4, UINT32)])
    fw.write(0, {0: 4, 1: 1, 2: 1, 3: 12345, 4: fit_ts(pts[0][0])})
    fw.define(1, 20, [(253, UINT32), (0, SINT32), (1, SINT32), (78, UINT32), (3, UINT8), (5, UINT32)])
    for ts, lat, lon, alt, hr, dist in pts:
        fw.write(20, {253: fit_ts(ts), 0: lat / SEMI, 1: lon / SEMI, 78: (alt + 500) * 5, 3: hr, 5: dist * 100})
    open(path, "wb").write(fw.bytes())


def write_strava_gpx(path, pts):
    rows = []
    for ts, lat, lon, alt, hr, _ in pts:
        for frac in (0, 1):  # Strava-style: two samples in the same second
            rows.append(f'<trkpt lat="{lat + frac * 1e-7:.7f}" lon="{lon:.7f}"><ele>{alt:.1f}</ele>'
                        f'<time>{ts:%Y-%m-%dT%H:%M:%SZ}</time><extensions><gpxtpx:TrackPointExtension>'
                        f'<gpxtpx:hr>{hr}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>')
    open(path, "w").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n<gpx creator="StravaGPX" version="1.1" '
        'xmlns="http://www.topografix.com/GPX/1/1" xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
        '<trk><name>Leg 6</name><trkseg>\n' + "\n".join(rows) + "\n</trkseg></trk></gpx>\n")


def make_project(root):
    if os.path.exists(root):
        shutil.rmtree(root)
    src = os.path.join(root, "sources")
    os.makedirs(src)
    mi = M_PER_MI
    legs_mi = {1: 1.5, 2: 1.0, 3: 2.0, 4: 1.2, 5: 1.0, 6: 1.3}
    start_d = {n: sum(legs_mi[k] for k in range(1, n)) * mi for n in legs_mi}
    dur = {n: round(legs_mi[n] * mi / SPEED) for n in legs_mi}

    # leg 1: A, ends at the handoff but the watch keeps going 40 s (still running)
    t1 = T0
    a1 = samples(t1, 0, dur[1] + 40, hr=150)
    write_fit(os.path.join(src, "A1.fit"), a1)
    # legs 2-3: B, starts exactly at the handoff and runs both legs on one watch
    t2 = t1 + timedelta(seconds=dur[1])
    b23 = samples(t2, start_d[2], dur[2] + dur[3], hr=160)
    write_fit(os.path.join(src, "B23.fit"), b23)
    # leg 4: C, watch started 60 s before the handoff while standing
    t4 = t2 + timedelta(seconds=dur[2] + dur[3])
    c4 = samples(t4 - timedelta(seconds=60), start_d[4], dur[4] + 60, hr=140, standing=60)
    write_fit(os.path.join(src, "C4.fit"), c4)
    shutil.copy(os.path.join(src, "C4.fit"), os.path.join(src, "C4.fit copy"))
    # leg 5: nobody recorded; leg 6 starts after the gap
    t6 = t4 + timedelta(seconds=dur[4] + dur[5])
    # leg 6: A ran along with C for the last 5 minutes of leg 4 (at C's position), then ran leg 6 from its start
    along = [(t6 - timedelta(seconds=dur[5] + 300 - s),) + point_at(start_d[5] - (300 - s) * SPEED) + (600.0, 120, 0.0)
             for s in range(0, 300)]
    a6 = samples(t6, start_d[6], dur[6], hr=155)
    write_strava_gpx(os.path.join(src, "A6.gpx"), along + a6)

    # cached route and elevation for the leg 5 gap: straight along the course, 25 m steps
    cache = os.path.join(root, "cache", "reconstruct")
    os.makedirs(cache)
    d_from, d_to = start_d[4] + dur[4] * SPEED, start_d[6]
    route = [list(point_at(d_from + k * (d_to - d_from) / 20)) for k in range(21)]
    json.dump(route, open(os.path.join(cache, "leg5_route.json"), "w"))
    n = int((d_to - d_from) // 25)
    elev = [list(point_at(d_from + k * (d_to - d_from) / n)) + [k * (d_to - d_from) / n, 620 + 10 * math.sin(k / 5)] for k in range(n + 1)]
    json.dump(elev, open(os.path.join(cache, "leg5_elevation.json"), "w"))

    event = {
        "slug": "TEST_RELAY", "event": "Test Alpine Relay 2026", "event_short": "Test Alpine Relay",
        "dates_label": "July 4, 2026", "team": "Unit Testers", "timezone": "Europe/Berlin", "sources": "sources",
        "course": {"summary": "Valley to Ridge", "start_name": "the valley", "start_short": "Valley",
                   "finish_name": "the ridge", "finish_short": "Ridge"},
        "official": {"run_time": "0:55:00", "pace": "7:14", "overall_place": 3, "category": "Mixed", "category_place": 1},
        "map": {"footer": ["<strong>Test:</strong> synthetic data."]},
        "flythrough": {"imagery": "esri", "block_size": 3},
    }
    json.dump(event, open(os.path.join(root, "event.json"), "w"), indent=2)
    open(os.path.join(root, "roster.csv"), "w").write(
        "leg,runner,miles,van,rating\n" + "".join(f"{n},{r},{legs_mi[n]},1,Easy\n" for n, r in
                                                   zip(range(1, 7), ["Ann", "Bo", "Bo", "Cy", "Cy", "Ann"])))
    open(os.path.join(root, "recordings.csv"), "w").write(
        "file,legs,start_utc,end_utc\nA1.fit,1,,\nB23.fit,2-3,,\nC4.fit,4,,\n"
        f"A6.gpx,6,{t6:%Y-%m-%d %H:%M:%S},\n")
    open(os.path.join(root, "gaps.csv"), "w").write(
        "id,legs,before,after,title,course\nleg5,5,C4.fit,A6.gpx,Leg 5,straight up the valley road\n")
    return dict(t1=t1, t2=t2, t4=t4, t6=t6, dur=dur, legs_mi=legs_mi)


if __name__ == "__main__":
    print(make_project(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_synthetic")))
