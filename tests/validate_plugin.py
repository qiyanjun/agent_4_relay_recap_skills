#!/usr/bin/env python3
"""Validate the relay-recap plugin layout and skill metadata.

Checks: manifests parse and agree on the name; SKILL.md frontmatter has name (matching its folder) and a
"Use when" description under 1024 characters; every script and reference the skill mentions exists; every Python
script compiles and shows usage when run without arguments; the map template has all the tokens build_map.py fills.
"""
import glob, json, os, py_compile, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skills", "recapping-a-relay")
errors = []


def err(msg):
    errors.append(msg)
    print(f"FAIL {msg}")


plugin = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
market = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
if plugin["name"] != "relay-recap" or market["plugins"][0]["name"] != plugin["name"]:
    err("plugin.json and marketplace.json must both name the plugin relay-recap")

text = open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8").read()
fm = re.match(r"---\n(.*?)\n---\n", text, re.S)
if not fm:
    err("SKILL.md has no frontmatter")
else:
    name = re.search(r"^name:\s*(.+)$", fm.group(1), re.M)
    desc = re.search(r"^description:\s*(.+)$", fm.group(1), re.M)
    if not name or name.group(1).strip() != os.path.basename(SKILL_DIR):
        err("frontmatter name must match the skill folder")
    if not desc or not desc.group(1).startswith("Use when") or len(desc.group(1)) > 1024:
        err("description must start with 'Use when' and be at most 1024 characters")

for ref in set(re.findall(r"references/[\w.-]+\.md", text)):
    if not os.path.exists(os.path.join(SKILL_DIR, ref)):
        err(f"SKILL.md mentions missing {ref}")
for script in set(re.findall(r"S/([\w.-]+\.(?:py|sh|mjs))", text)):
    if not os.path.exists(os.path.join(SKILL_DIR, "scripts", script)):
        err(f"SKILL.md mentions missing scripts/{script}")

for path in sorted(glob.glob(os.path.join(SKILL_DIR, "scripts", "*.py"))):
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        err(f"{os.path.basename(path)} does not compile: {e}")
        continue
    if os.path.basename(path) == "relaylib.py":
        continue
    r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=60)
    if r.returncode == 0 or not (r.stdout + r.stderr).strip():
        err(f"{os.path.basename(path)} without arguments should exit non-zero with usage")

template = open(os.path.join(SKILL_DIR, "scripts", "map_template.html"), encoding="utf-8").read()
for token in ("__TITLE__", "__DESCRIPTION__", "__DATA__", "__BASEMAP_LIGHT__", "__BASEMAP_DARK__"):
    if token not in template:
        err(f"map_template.html lacks {token}")

print("plugin valid" if not errors else f"{len(errors)} problem(s)")
sys.exit(1 if errors else 0)
