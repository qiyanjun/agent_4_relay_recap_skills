"""End-to-end tests on a synthetic relay (tests/synthetic.py): no real data, no network.

Run: python3 -m pytest tests/ -q
The snapshot test needs Node packages and Chrome: it uses $RELAY_RECAP_NODE_DIR or tests/.node (a folder holding
node_modules with puppeteer-core, e.g. from `bash skills/recapping-a-relay/scripts/node_setup.sh <project>`), and is
skipped otherwise.
"""
import csv, json, os, re, subprocess, sys
from datetime import datetime, timedelta
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "skills", "recapping-a-relay", "scripts")
sys.path.insert(0, HERE)
from synthetic import make_project  # noqa: E402


def run(script, project, *args, ok=True):
    env = dict(os.environ, RELAY_RECAP_OFFLINE="1")
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), project, *args], capture_output=True, text=True, env=env)
    if ok:
        assert r.returncode == 0, f"{script} failed:\n{r.stdout}\n{r.stderr}"
    return r


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("relay") / "project")
    info = make_project(root)
    info["root"] = root
    info["recon"] = run("reconstruct_gaps.py", root).stdout
    info["combine"] = run("build_combined.py", root).stdout
    return info


def out(project, suffix):
    return os.path.join(project["root"], "out", f"TEST_RELAY_{suffix}")


def manifest(project):
    return {int(r["leg"]): r for r in csv.DictReader(open(out(project, "legs_manifest.csv")))}


def records(project):
    return list(csv.DictReader(open(out(project, "merged_records.csv"))))


def ts(text):
    return datetime.fromisoformat(text)


def test_duplicate_copy_is_skipped_and_reported(project):
    assert "not in recordings.csv, skipped: C4.fit copy" in project["combine"]


def test_running_past_handoff_trims_previous_tail(project):
    m = manifest(project)
    assert "trimmed end of A1.fit" in project["combine"]
    assert ts(m[1]["end_utc"]) < ts(m[2]["start_utc"])
    assert ts(m[2]["start_utc"]) == project["t2"]


def test_standing_early_start_trims_next_head(project):
    m = manifest(project)
    assert "trimmed start of C4.fit" in project["combine"]
    assert ts(m[4]["start_utc"]) > project["t4"] - timedelta(seconds=2)


def test_two_leg_recording_is_split_by_official_miles(project):
    m = manifest(project)
    assert m[2]["recorded_files"] == m[3]["recorded_files"] == "B23.fit"
    assert "split by official leg miles" in m[2]["note"]
    total = float(m[2]["miles"]) + float(m[3]["miles"])
    assert abs(float(m[2]["miles"]) / total - 1 / 3) < 0.01


def test_gap_is_rebuilt_flagged_and_included_automatically(project):
    m = manifest(project)
    assert "TEST_RELAY_leg5_reconstructed.gpx" in project["recon"]
    assert m[5]["reconstructed_files"] == "TEST_RELAY_leg5_reconstructed.gpx"
    assert float(m[5]["reconstructed_miles"]) >= float(m[5]["miles"]) - 0.05
    assert m[5]["avg_hr"] == ""  # no heart rate is invented
    leg5 = [r for r in records(project) if r["leg"] == "5"]
    assert {r["source"] for r in leg5} == {"reconstructed"}


def test_ran_along_window_drops_early_records_for_leg_and_gap(project):
    m = manifest(project)
    assert ts(m[6]["start_utc"]) >= project["t6"]
    assert "from 2026-07-04" in m[6]["note"] and "ran along" in m[6]["note"]
    # the rebuilt leg 5 must end at A6's windowed start, not at its ran-along start 5 minutes earlier
    assert ts(m[5]["end_utc"]) >= project["t6"] - timedelta(seconds=2)


def test_strava_multi_sample_seconds_are_collapsed(project):
    stamps = [r["timestamp_utc"] for r in records(project)]
    assert len(stamps) == len(set(stamps))
    assert stamps == sorted(stamps)


def test_distance_is_continuous(project):
    d = [float(r["distance_m"]) for r in records(project)]
    assert all(b >= a for a, b in zip(d, d[1:]))


def test_verify_fit_passes_and_strava_copy_has_no_heart_rate(project):
    r = run("verify_fit.py", project["root"])
    assert "ALL CHECKS PASSED" in r.stdout
    assert "records with heart rate 0, laps with heart rate 0" in r.stdout


def test_gpx_tracks_are_named_per_leg(project):
    gpx = open(out(project, "combined.gpx")).read()
    assert re.findall(r"<name>(Leg \d \| \w+)</name>", gpx) == [
        "Leg 1 | Ann", "Leg 2 | Bo", "Leg 3 | Bo", "Leg 4 | Cy", "Leg 5 | Cy", "Leg 6 | Ann"]
    assert "<type>other</type>" in gpx


def test_strava_description(project):
    run("strava_description.py", project["root"])
    text = open(out(project, "strava_description.txt")).read()
    assert text.startswith("TITLE\nTest Alpine Relay 2026 - Team Unit Testers, 6 legs, 3 runners")
    assert "Legs (start CEST | miles | time | runner):" in text
    assert "5. Sat 8:" in text and "Cy (rebuilt)" in text
    assert "No recording for leg 5:" in text
    assert "3rd overall, 1st Mixed" in text
    assert "Ann: legs 1, 6" in text


def test_route_map_builds_offline(project):
    r = run("build_map.py", project["root"])
    assert "basemap tiles could not be downloaded" in r.stdout
    html = open(out(project, "route_map.html")).read()
    for token in ("__TITLE__", "__DESCRIPTION__", "__DATA__", "__BASEMAP_LIGHT__", "__BASEMAP_DARK__"):
        assert token not in html
    data = json.load(open(out(project, "map_data.json")))
    assert data["coverage"]["recon_leg_numbers"] == [5]
    assert data["event"]["has_van"] and data["event"]["has_rating"] and not data["event"]["has_hm_pace"]
    assert data["nights"] == []  # a morning race has no night band
    assert data["event"]["tz_abbr"] == "CEST"


def test_flythrough_scene_exports_without_places(project):
    r = run("export_flythrough.py", project["root"])
    assert "run geocode_places.py" in r.stdout
    scene = json.load(open(os.path.join(project["root"], "out", "flythrough", "scene.json")))
    assert [L["n"] for L in scene["legs"]] == [1, 2, 3, 4, 5, 6]
    assert scene["options"]["block_size"] == 3 and scene["timezone"] == "Europe/Berlin"
    assert scene["tiles"]["imagery"].startswith("https://server.arcgisonline.com")
    assert scene["tz_abbrs"][0][1] == "CEST"
    assert [L["tag"] for L in scene["legs"]][4] == "rebuilt"
    colors = [L["color"] for L in scene["legs"]]
    assert colors[0] == colors[5] and len({colors[0], colors[1], colors[3]}) == 3


def test_missing_leg_coverage_fails_clearly(tmp_path):
    root = str(tmp_path / "bad")
    make_project(root)
    os.remove(os.path.join(root, "gaps.csv"))
    r = run("build_combined.py", root, ok=False)
    assert r.returncode != 0 and "every leg needs a recording or a reconstruction" in (r.stdout + r.stderr)


def test_empty_config_values_mean_not_given(tmp_path):
    root = str(tmp_path / "blank")
    make_project(root)
    ev = json.load(open(os.path.join(root, "event.json")))
    ev["official"] = {"run_time": "", "start": "", "overall_place": None}
    json.dump(ev, open(os.path.join(root, "event.json"), "w"))
    for script in ("reconstruct_gaps.py", "build_combined.py", "strava_description.py", "build_map.py"):
        run(script, root)
    text = open(os.path.join(root, "out", "TEST_RELAY_strava_description.txt")).read()
    assert ": official " not in text and "overall" not in text


def node_dir():
    for cand in (os.environ.get("RELAY_RECAP_NODE_DIR"), os.path.join(HERE, ".node")):
        if cand and os.path.isdir(os.path.join(cand, "node_modules", "puppeteer-core")):
            return cand
    return None


@pytest.mark.skipif(node_dir() is None, reason="needs installed Node packages (run node_setup.sh on a project)")
def test_route_map_renders_without_page_errors(project, tmp_path):
    run("build_map.py", project["root"])
    nd = node_dir()
    subprocess.run(["cp", os.path.join(SCRIPTS, "snapshot_map.mjs"), os.path.join(SCRIPTS, "chrome.mjs"), nd], check=True)
    prefix = str(tmp_path / "snap")
    r = subprocess.run(["node", os.path.join(nd, "snapshot_map.mjs"), out(project, "route_map.html"), prefix],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("phone", "overview", "map"):
        assert os.path.getsize(f"{prefix}_{name}.png") > 10_000
