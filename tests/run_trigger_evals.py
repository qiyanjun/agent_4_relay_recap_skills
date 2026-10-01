#!/usr/bin/env python3
"""Measure which of this plugin's skills Claude picks for each query in evals/routing.json.

Each query runs through `claude -p` in an empty scratch directory with only this
plugin loaded (user settings skipped, so other installed plugins and their
look-alike skills cannot compete). The first Skill call — or a Read of a
skills/<name>/SKILL.md — is the pick; the process is killed as soon as it is
seen. A plugin command counts as the skill it invokes. No pick is recorded as
"none"; a pick of another plugin's skill as "other:<name>"; a run that hit a
usage limit or API error as "error" (never a pass).

--installed instead runs with your real setup (user settings, every installed
plugin and skill, MCP servers) and tests the plugin version you have
installed, not this checkout. It measures collisions: a request meant for
this plugin that goes to a look-alike skill elsewhere counts as a miss.

The plugin directory and name come from .claude-plugin/marketplace.json.

Needs a logged-in `claude` CLI; it is not run in CI. Costs one short model
call per query per run.

  python3 tests/run_trigger_evals.py                   # all cases, 3 runs each
  python3 tests/run_trigger_evals.py --runs 1 --only relay-01 neg-03
  python3 tests/run_trigger_evals.py --installed       # against your real setup
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENTRY = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8"))["plugins"][0]
_SOURCE = _ENTRY["source"]["path"] if isinstance(_ENTRY["source"], dict) else _ENTRY["source"]
PLUGIN = os.path.normpath(os.path.join(ROOT, _SOURCE))
NAME = _ENTRY["name"]
EVALS = os.path.join(ROOT, "evals", "routing.json")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")
PLUGIN_NS = NAME + ":"
BLOCKED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Agent"]


def plugin_names() -> tuple[set, dict]:
    """Skill names, and command name -> the skill it invokes."""
    skills = {os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(PLUGIN, "skills", "*", "SKILL.md"))}
    commands = {}
    for path in glob.glob(os.path.join(PLUGIN, "commands", "*.md")):
        named = re.findall(r"\bthe `?([a-z][a-z0-9-]*[a-z0-9])`? skill\b", open(path, encoding="utf-8").read())
        commands[os.path.splitext(os.path.basename(path))[0]] = next((n for n in named if n in skills), None)
    return skills, commands


def resolve(raw: str, skills: set, commands: dict) -> str:
    """Map a Skill call or SKILL.md path to a plugin skill name, or 'other:<name>'."""
    name = raw.strip().lstrip("/")
    path_match = re.search(r"skills/([^/]+)/SKILL\.md", name)
    if path_match:
        # A path inside this checkout (plugin-only mode) or the installed plugin cache (.../relay-recap/...)
        inside = os.path.realpath(name).startswith(os.path.realpath(PLUGIN) + os.sep) or f"/{NAME}/" in name
        if not inside:
            return f"other:{path_match.group(1)}"
        name = path_match.group(1)
    elif name.startswith(PLUGIN_NS):
        name = name[len(PLUGIN_NS):]
    elif ":" in name:
        return f"other:{name}"
    if name in skills:
        return name
    target = commands.get(name)
    return target if target in skills else f"other:{name}"


def run_query(query: str, model: str, timeout: int, skills: set, commands: dict, installed: bool) -> tuple[str, str]:
    """Return (pick, raw) for one run of one query."""
    cmd = ["claude", "-p", query, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    if not installed:
        cmd += ["--setting-sources", "project", "--strict-mcp-config", "--plugin-dir", PLUGIN]
    cmd += ["--disallowedTools", *BLOCKED_TOOLS]
    if model:
        cmd += ["--model", model]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    with tempfile.TemporaryDirectory(prefix="relay-route-") as cwd:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=cwd, env=env, text=True)
        deadline = time.time() + timeout
        try:
            for line in proc.stdout:
                if time.time() > deadline:
                    return "none", "timeout"
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "result":
                    # A usage limit or API error is not a routing decision; don't score it as "none".
                    text = str(event.get("result") or "")
                    if event.get("is_error") or re.search(r"hit your (session|usage) limit|API Error", text):
                        return "error", f"error: {text[:120]}"
                    return "none", "answered without a skill"
                if event.get("type") != "assistant":
                    continue
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") != "tool_use":
                        continue
                    tool, args = block.get("name"), block.get("input", {})
                    if tool == "Skill":
                        raw = args.get("skill", "")
                        return resolve(raw, skills, commands), raw
                    if tool == "Read" and args.get("file_path", "").endswith("SKILL.md"):
                        raw = args["file_path"]
                        return resolve(raw, skills, commands), raw
            return "none", "no skill call"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=3, help="runs per query (default 3)")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=90, help="seconds per run")
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--only", nargs="*", help="case ids to run")
    parser.add_argument("--installed", action="store_true", help="run against your real installed setup")
    args = parser.parse_args()

    cases = json.load(open(EVALS, encoding="utf-8"))["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    skills, commands = plugin_names()
    unknown = {s for c in cases for s in c["expect"]} - skills
    if unknown:
        sys.exit(f"evals/routing.json expects unknown skills: {sorted(unknown)}")

    picks = defaultdict(list)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_query, c["query"], args.model, args.timeout, skills, commands, args.installed): c["id"]
            for c in cases for _ in range(args.runs)
        }
        for i, future in enumerate(as_completed(futures), 1):
            case_id = futures[future]
            try:
                picks[case_id].append(future.result())
            except Exception as e:  # a crashed run is an error, not a pick
                picks[case_id].append(("error", f"error: {e}"))
            print(f"\r{i}/{len(futures)} runs", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    rows, passed = [], 0
    for c in cases:
        counts = Counter(p for p, _ in picks[c["id"]])
        top, top_n = counts.most_common(1)[0]
        # A run that errored (usage limit, API failure) never counts as a pass.
        ok = top != "error" and (top in c["expect"] if c["expect"] else top not in skills)
        passed += ok
        rows.append({
            "id": c["id"], "expect": c["expect"] or ["none"], "picks": dict(counts),
            "majority": top, "agreement": f"{top_n}/{len(picks[c['id']])}", "pass": ok,
            "raw": [r for _, r in picks[c["id"]]], "query": c["query"],
        })

    # Recall is credited to a case's first expected skill; a false fire is a
    # skill winning a case it was not expected on.
    recall, false_fires = defaultdict(lambda: [0, 0]), Counter()
    for r in rows:
        primary = r["expect"][0]
        if primary != "none":
            recall[primary][1] += 1
            recall[primary][0] += r["pass"]
        if not r["pass"] and r["majority"] in skills:
            false_fires[r["majority"]] += 1

    mode = "installed setup" if args.installed else "this checkout only"
    print(f"\nRouting accuracy: {passed}/{len(rows)} cases ({mode}; majority of {args.runs} runs, model {args.model})\n")
    print(f"{'skill':28} {'recall':>8} {'false fires':>12}")
    for s in sorted(skills):
        hit, total = recall[s]
        print(f"{s:28} {f'{hit}/{total}':>8} {false_fires[s]:>12}")
    stolen = Counter()
    for r in rows:
        if r["expect"] != ["none"]:
            stolen.update({p: n for p, n in r["picks"].items() if p.startswith("other:")})
    if stolen:
        print(f"\nOther skills picked on {NAME} requests (runs):")
        for name, n in stolen.most_common():
            print(f"  {name:40} {n}")
    failures = [r for r in rows if not r["pass"]]
    if failures:
        print("\nMisroutes:")
        for r in failures:
            print(f"  {r['id']:11} expected {'/'.join(r['expect']):28} got {r['majority']} ({r['agreement']})  {r['query'][:70]}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(RESULTS_DIR, f"routing-{'installed-' if args.installed else ''}{stamp}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"model": args.model, "runs": args.runs, "mode": mode, "passed": passed, "total": len(rows), "cases": rows}, f, indent=2)
    print(f"\nSaved {os.path.relpath(out, ROOT)}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
