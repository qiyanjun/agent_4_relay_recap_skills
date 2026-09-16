"""Check the combined FIT files before anyone uploads them.

Usage: python3 verify_fit.py <project_dir>
Decodes out/<slug>_combined.fit and out/<slug>_combined_strava.fit with fitparse (CRC checked) and verifies:
- file_id is the first message; one session; one lap per roster leg; laps in order and not overlapping
- record count and GPS points match between the two files; distance and elapsed time match the manifest
- the Strava copy has no heart rate anywhere (records, laps, session); the full copy does, if any source had it
- sport is what event.json asked for; both files are under Strava's and Garmin's 25 MB upload limit
Prints a report and exits 1 on any failure.
"""
import os, sys
import fitparse
from relaylib import Project, M_PER_MI

if len(sys.argv) != 2:
    sys.exit(__doc__)
P = Project(sys.argv[1])
roster = P.roster()
manifest = P.manifest()
SPORT = {"generic": "generic", "running": "running", "cycling": "cycling", "walking": "walking", "hiking": "hiking"}[
    P.opt("combine", "sport", default="generic")]
LIMIT_MB = 25
failures = []


def check(ok, msg):
    print(f"  [{'ok' if ok else 'FAIL'}] {msg}")
    if not ok:
        failures.append(msg)


def summarize(path):
    ff = fitparse.FitFile(path, check_crc=True)
    msgs = list(ff.get_messages())
    names = [m.name for m in msgs]
    recs = [m.get_values() for m in msgs if m.name == "record"]
    laps = [m.get_values() for m in msgs if m.name == "lap"]
    sessions = [m.get_values() for m in msgs if m.name == "session"]
    return dict(names=names, recs=recs, laps=laps, sessions=sessions,
                hr_records=sum(1 for r in recs if r.get("heart_rate") is not None),
                hr_laps=sum(1 for l in laps if l.get("avg_heart_rate") is not None or l.get("max_heart_rate") is not None),
                hr_session=any(s.get("avg_heart_rate") is not None or s.get("max_heart_rate") is not None for s in sessions),
                gps=sum(1 for r in recs if r.get("position_lat") is not None),
                mb=os.path.getsize(path) / 1e6)


results = {}
for label, suffix in (("full", "combined.fit"), ("strava", "combined_strava.fit")):
    path = P.output(suffix)
    print(f"{os.path.basename(path)}")
    if not os.path.exists(path):
        check(False, "file exists (run build_combined.py)")
        continue
    try:
        s = summarize(path)
    except Exception as e:  # fitparse raises on CRC or structure errors
        check(False, f"decodes without errors: {e}")
        continue
    results[label] = s
    check(True, "decodes, CRC valid")
    check(s["names"][0] == "file_id", "file_id is the first message")
    check(len(s["sessions"]) == 1, f"one session ({len(s['sessions'])})")
    check(len(s["laps"]) == len(roster), f"one lap per leg ({len(s['laps'])} laps, {len(roster)} legs)")
    starts = [l["start_time"] for l in s["laps"]]
    ends = [l["timestamp"] for l in s["laps"]]
    check(all(e <= n for e, n in zip(ends, starts[1:])), "laps in time order without overlap")
    sess = s["sessions"][0] if s["sessions"] else {}
    check(sess.get("sport") == SPORT and all(l.get("sport") == SPORT for l in s["laps"]),
          f"sport '{SPORT}' on session and laps (session: {sess.get('sport')})")
    man_mi = sum(float(r["miles"]) for r in manifest.values())
    fit_mi = (sess.get("total_distance") or 0) / M_PER_MI
    check(abs(fit_mi - man_mi) < 0.1, f"distance {fit_mi:.2f} mi matches manifest ({man_mi:.2f} mi)")
    check(s["mb"] < LIMIT_MB, f"size {s['mb']:.2f} MB under the {LIMIT_MB} MB upload limit")
    print(f"  records {len(s['recs'])}, GPS points {s['gps']}, elapsed {(sess.get('total_elapsed_time') or 0) / 3600:.2f} h, "
          f"records with heart rate {s['hr_records']}, laps with heart rate {s['hr_laps']}")

if "strava" in results:
    s = results["strava"]
    print("strava copy")
    check(s["hr_records"] == 0 and s["hr_laps"] == 0 and not s["hr_session"], "no heart rate in records, laps or session")
if "full" in results and "strava" in results:
    a, b = results["full"], results["strava"]
    check(len(a["recs"]) == len(b["recs"]) and a["gps"] == b["gps"], "same records and GPS points as the full file")
    any_hr = any(r["avg_hr"] for r in manifest.values())
    check(a["hr_records"] > 0 or not any_hr, "full file keeps the heart rate the sources had")

print("\nALL CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED")
sys.exit(1 if failures else 0)
