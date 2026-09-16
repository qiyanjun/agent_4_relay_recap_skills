"""Write a Strava title and description for the combined relay activity.

Usage: python3 strava_description.py <project_dir>
Inputs: event.json (event, team, course summary, optional official result), roster.csv, out/<slug>_legs_manifest.csv
and out/<slug>_merged_records.csv (build_combined.py).
Output: out/<slug>_strava_description.txt, plain ASCII: paste the title and the description into Strava.
"""
import sys
from relaylib import Project, utc_naive, hmm, ordinal, join_and, legs_label, rebuilt_tag, ascii_only

if len(sys.argv) != 2:
    sys.exit(__doc__)
P = Project(sys.argv[1])
E, off = P.event, P.event.get("official") or {}
roster, manifest = P.roster(), P.manifest()
runners = P.runner_order(roster)


def tag(n):
    t = rebuilt_tag(float(manifest[n]["reconstructed_miles"]), float(manifest[n]["miles"]))
    return f" ({t})" if t else ""


# where the rebuilt part of a partly rebuilt leg sits: from the source order of its records
first_source, last_source = {}, {}
for r in P.merged_records():
    n = int(r["leg"])
    first_source.setdefault(n, r["source"])
    last_source[n] = r["source"]

rebuilt = [n for n in sorted(manifest) if tag(n) == " (rebuilt)"]
part = [n for n in sorted(manifest) if tag(n) == " (part rebuilt)"]
course_mi = round(sum(float(r["miles"]) for r in roster.values()), 1)
title = f"{E['event']} - Team {E['team']}, {len(roster)} legs, {len(runners)} runners"

summary = P.opt("course", "summary")
lines = [f"{E['event']}: {summary + '. ' if summary else ''}{len(roster)} legs, {course_mi} mi."]
if off.get("run_time"):
    details = ", ".join(str(x) for x in (f"bib {off['bib']}" if off.get("bib") else None, off.get("category"), off.get("city")) if x)
    result = f"Team {E['team']}{f' ({details})' if details else ''}: official {off['run_time'].split('.')[0]}"
    if off.get("pace"):
        result += f", {off['pace']} /mi"
    if off.get("overall_place"):
        result += f", {ordinal(off['overall_place'])} overall"
    if off.get("category_place") and off.get("category"):
        result += f", {ordinal(off['category_place'])} {off['category']}"
    lines.append(result + ".")
lines += ["", f"A team relay stitched together from {len(runners)} runners' watches, not one person's run. Heart rate is removed."]
missing = []
if rebuilt:
    missing.append(f"legs {join_and(rebuilt)}" if len(rebuilt) > 1 else f"leg {rebuilt[0]}")
for n in part:
    mi = float(manifest[n]["reconstructed_miles"])
    where = "first" if first_source[n] == "reconstructed" else "last" if last_source[n] == "reconstructed" else None
    missing.append(f"the {where} {mi:.1f} mi of leg {n}" if where else f"{mi:.1f} mi of leg {n}")
if missing:
    lines.append(f"No recording for {join_and(missing)}: those parts follow the official course with times fitted "
                 "between the handoffs.")
tz = P.tz_abbr(utc_naive(manifest[1]["start_utc"]))
lines += ["", f"Legs (start {tz} | miles | time | runner):"]
width = len(str(len(roster)))
for n in sorted(roster):
    ro, row = roster[n], manifest[n]
    lines.append(f"{n:>{width}}. {P.clock(utc_naive(row['start_utc']))} | {ro['miles']} mi | "
                 f"{hmm(float(row['duration_min']))} | {ro['runner']}{tag(n)}")
lines += ["", "Runners:"]
for name in runners:
    ns = [n for n in sorted(roster) if roster[n]["runner"] == name]
    lines.append(f"{name}: {'leg' if len(ns) == 1 else 'legs'} {legs_label(ns)}")
description = "\n".join(lines)

text = "TITLE\n" + title + "\n\nDESCRIPTION\n" + description + "\n"
ascii_only(text, "the Strava description")
out = P.output("strava_description.txt")
open(out, "w", encoding="ascii").write(text)
print(f"{out}: title {len(title)} chars, description {len(description)} chars, {len(lines)} lines")
