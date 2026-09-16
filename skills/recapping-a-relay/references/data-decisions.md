# Data decisions

These are the judgment calls behind a combined relay record, learned on a real overnight team relay.

## Matching files to legs

- Start from the `new_project.py` inventory, sorted by start time. File names are hints, not facts: a file named
  "Leg 11 and 12" can be a runner pacing a teammate through leg 11 (whose own watch recorded it) and then
  running leg 12.
- Check each file's duration and distance against the roster leg; a big mismatch means a wrong leg, a watch paused
  mid-leg, or two legs on one watch.
- Finder duplicates ("x.fit copy", "x.fit 2") are byte-identical and skipped. Different bytes with the same start
  time are two watches on the same leg: pick one (normally the leg runner's own) or use a time window.
- Exports differ: Strava GPX has several samples per second (collapsed to one); some GPX lack heart rate; GPX made
  by another reconstruction tool is recognized when its creator or description says "reconstructed".

## Who ran the leg

- The team sheet is the plan. Watches record what happened; where they disagree, the watch wins (swapped legs,
  substitutions). A team's own later correction beats both.
- Keep the plan visible: `plan_runner` and `note` in the roster. The map page lists the legs that differ and why.
- A watch belongs to a person, not a runner slot: a borrowed watch or a shared account can record a teammate's leg,
  and the file then carries the wrong name. Say so in the note.

## Handoff overlaps (automatic)

When two consecutive recordings overlap, exactly one side is cut, so no second is counted twice:

1. Recorded beats reconstructed: the rebuilt side loses the overlap.
2. The next watch started early and moved less than `combine.standing_m` (50 m) during the overlap: the runner was
   still waiting at the exchange, so the next file loses its head.
3. Otherwise the previous runner stopped their watch late: the previous file loses its tail.

Typical overlaps are seconds to a couple of minutes. `build_combined.py` prints each one; read them.

## Two watches on one leg (not automatic)

An overlap of many minutes means two people recorded the same stretch: a pacer or a teammate running along, or a
watch left on in the van. The overlap rule would cut the wrong thing. Decide which recording is the leg, then give
the other one a `start_utc` or `end_utc` so only its own leg remains. Find the moment where its track leaves the
exchange or where the other watch ends.

## One watch, several legs

A runner who ran consecutive legs, or a watch not stopped at an exchange, gives one file covering several legs. It is
split by the legs' official miles (a 3.0 + 4.0 mi file splits at 3.0/7.0 of its recorded distance). The split point
is approximate; the note says so.

## Unrecorded legs

- Never drop a leg: the combined activity would teleport and the lap count would be wrong.
- Rebuild it from the course: OSM foot routing between the neighbouring GPS fixes, DEM elevation, times spread across
  the gap with a grade-adjusted pace (slower uphill, fastest around -10%). This matches what GOTOES-style timestamp
  tools do.
- Verify: printed routed miles should be close to the official leg miles and the pace plausible. Compare with the cue
  sheet. Routing prefers footpaths and shortcuts; force the race road with `via` waypoints.
- Label it everywhere: GPX description, manifest `reconstructed_miles`, map ("Rebuilt", dashed line), Strava text
  ("(rebuilt)"), flythrough panel. Never add heart rate or cadence to rebuilt parts.

## The activity files

- One session, one lap per leg (lap N = leg N), timer stop/start at each exchange, continuous distance.
- FIT laps cannot carry names; the GPX has one track per leg named "Leg N | Runner".
- Sport "generic" (Other): a relay is not one athlete's run and must stay off running personal records and segment
  leaderboards (Strava's guidelines ask for a different type or private visibility).
- Strava copy: no heart rate at all, because many people's heart rate would be read as one athlete's.
- Garmin requires `file_id` first; Garmin and Strava cap uploads at 25 MB. `verify_fit.py` checks both.
- Upload the Strava copy once, privacy "Only You", paste the title and description; check the type after upload
  (Strava may still guess Run). Keep `_combined.fit` and the GPX as the full team record.
