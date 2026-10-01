#!/usr/bin/env python3
"""Validate the relay-recap plugin layout and skill metadata.

The plugin directory comes from marketplace.json plugins[0].source ("./" = the repo root). Checks:
  1. every skills/<dir>/SKILL.md has strict-YAML frontmatter with `name` (matching its folder) and a
     `description` that starts with "Use when" and is at most 1024 characters
  2. plugin.json and marketplace.json agree on the plugin name, and on the version if the marketplace entry
     sets one (a bump that touches only one of them ships a stale cache)
  3. every commands/*.md (if any) has valid frontmatter with a `description`, and any skill it names exists
  4. every `/relay-recap:<cmd>` mention resolves to commands/<cmd>.md, and every backticked
     `relay-recap:<name>` mention to a skill or command
  5. every references/..., scripts/..., S/<script> or ${CLAUDE_PLUGIN_ROOT}/... path the skill docs mention
     exists, and so does every bare script file name (foo.py, foo.sh, foo.mjs) they mention
  6. every Python script compiles and, run without arguments, exits non-zero with usage; every .sh passes
     `bash -n`; every .mjs passes `node --check` (when Node is installed)
  7. map_template.html has every token build_map.py fills
  8. <name>.plugin, if present, matches the plugin directory byte for byte
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: pip install pyyaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_DESC = 1024
BUNDLE_IGNORE = {".DS_Store", ".gitignore"}
# Not part of the shipped plugin when the plugin dir is the repo root.
NOT_SHIPPED = {"__pycache__", ".git", ".github", ".pytest_cache", "node_modules", "tests", "evals"}
MAP_TOKENS = ("__TITLE__", "__DESCRIPTION__", "__DATA__", "__BASEMAP_LIGHT__", "__BASEMAP_DARK__")


def rel(path):
    return os.path.relpath(path, ROOT)


def plugin_dir(errors):
    market_json = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    try:
        market = json.load(open(market_json, encoding="utf-8"))
        source = market["plugins"][0]["source"]
        if isinstance(source, dict):  # e.g. {"source": "git-subdir", "path": "<dir>"}
            source = source.get("path", ".")
    except (OSError, json.JSONDecodeError, KeyError, IndexError) as e:
        errors.append(f"{rel(market_json)}: unreadable or has no plugin entry: {e}")
        return None
    path = os.path.normpath(os.path.join(ROOT, source))
    if not os.path.isdir(path):
        errors.append(f"marketplace source {source!r} is not a directory")
        return None
    return path


def frontmatter(path):
    text = open(path, encoding="utf-8").read()
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def check_skills(errors, plugin):
    names = set()
    for skill_md in sorted(glob.glob(os.path.join(plugin, "skills", "*", "SKILL.md"))):
        dirname = os.path.basename(os.path.dirname(skill_md))
        names.add(dirname)
        fm = frontmatter(skill_md)
        if fm is None:
            errors.append(f"{rel(skill_md)}: frontmatter missing or not valid YAML")
            continue
        if fm.get("name") != dirname:
            errors.append(f"{rel(skill_md)}: name is {fm.get('name')!r} but directory is {dirname!r}")
        desc = " ".join(str(fm.get("description") or "").split())
        if not desc:
            errors.append(f"{rel(skill_md)}: frontmatter missing `description`")
        elif len(desc) > MAX_DESC:
            errors.append(f"{rel(skill_md)}: description is {len(desc)} chars (max {MAX_DESC})")
        elif not desc.startswith("Use when"):
            errors.append(f"{rel(skill_md)}: description should start with 'Use when'")
    if not names:
        errors.append(f"no skills found under {rel(plugin)}/skills")
    return names


def check_manifests(errors, plugin):
    plugin_json = os.path.join(plugin, ".claude-plugin", "plugin.json")
    market_json = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    try:
        meta = json.load(open(plugin_json, encoding="utf-8"))
        market = json.load(open(market_json, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        errors.append(f"manifest unreadable: {e}")
        return None
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(meta.get("version", ""))):
        errors.append(f"{rel(plugin_json)}: version {meta.get('version')!r} is not MAJOR.MINOR.PATCH")
    entries = [p for p in market.get("plugins", []) if p.get("name") == meta.get("name")]
    if not entries:
        errors.append(f"marketplace.json has no entry named {meta.get('name')!r}")
    elif "version" in entries[0] and entries[0]["version"] != meta.get("version"):
        errors.append(f"version mismatch: plugin.json {meta.get('version')} vs marketplace.json {entries[0]['version']}")
    return meta.get("name")


def check_commands(errors, plugin, skills):
    commands = set()
    for cmd in sorted(glob.glob(os.path.join(plugin, "commands", "*.md"))):
        commands.add(os.path.splitext(os.path.basename(cmd))[0])
        fm = frontmatter(cmd)
        if fm is None:
            errors.append(f"{rel(cmd)}: frontmatter missing or not valid YAML (quote values that start with `[`)")
        elif not fm.get("description"):
            errors.append(f"{rel(cmd)}: frontmatter missing `description`")
        text = open(cmd, encoding="utf-8").read()
        for skill in re.findall(r"\bthe `?([a-z][a-z0-9:-]*[a-z0-9])`? skill\b", text):
            if ":" not in skill and skill not in skills:
                errors.append(f"{rel(cmd)}: refers to unknown skill {skill!r}")
    return commands


def check_links(errors, plugin, name, skills, commands):
    ns = re.escape(name)
    skills_dir = os.path.join(plugin, "skills")
    docs = sorted(glob.glob(os.path.join(skills_dir, "**", "*.md"), recursive=True))
    docs += sorted(glob.glob(os.path.join(plugin, "commands", "*.md")))
    docs += [p for p in (os.path.join(ROOT, "README.md"),) if os.path.exists(p)]

    for path in docs:
        skill_dir = None
        parts = os.path.relpath(path, skills_dir).split(os.sep)
        if parts[0] != ".." and len(parts) > 1:
            skill_dir = os.path.join(skills_dir, parts[0])
        here = os.path.dirname(path)
        script_names = set()
        if skill_dir:
            script_names = {os.path.basename(p) for p in glob.glob(os.path.join(skill_dir, "scripts", "*"))}

        for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
            where = f"{rel(path)}:{lineno}"
            for cmd in re.findall(rf"/{ns}:([a-z][a-z0-9-]*)", line):
                if cmd not in commands:
                    errors.append(f"{where}: /{name}:{cmd} has no commands/{cmd}.md")
            for ref in re.findall(rf"`{ns}:([a-z][a-z0-9-]*)`", line):
                if ref not in skills and ref not in commands:
                    errors.append(f"{where}: `{name}:{ref}` is not a skill or command in this plugin")
            for ref in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+\.(?:md|sh|py|js|mjs|html))", line):
                if not os.path.exists(os.path.join(plugin, ref)):
                    errors.append(f"{where}: ${{CLAUDE_PLUGIN_ROOT}}/{ref} does not exist")
            if skill_dir is None:
                continue
            # references/x.md, scripts/x.py (backticked or not), relative to the skill, this file or the plugin
            for ref in re.findall(r"(?<![\w/])((?:\.\./)*(?:references|scripts)/[A-Za-z0-9_-][A-Za-z0-9_-]*"
                                  r"\.(?:md|sh|py|js|mjs|html))", line):
                candidates = [os.path.join(skill_dir, ref), os.path.join(here, ref), os.path.join(plugin, ref)]
                if not any(os.path.exists(os.path.normpath(c)) for c in candidates):
                    errors.append(f"{where}: {ref} does not exist")
            # S/<script> is the skill docs' shorthand for this skill's scripts/ directory
            for script in re.findall(r"(?<![\w/])S/([A-Za-z0-9_-]+\.(?:py|sh|mjs))", line):
                if script not in script_names:
                    errors.append(f"{where}: S/{script} is not in scripts/")
            # bare script names: snapshot_map.mjs, capture.mjs, add_music.py, ...
            for script in re.findall(r"(?<![\w./-])([a-z][a-z0-9_]*\.(?:py|sh|mjs))\b", line):
                if script not in script_names:
                    errors.append(f"{where}: mentions {script}, which is not in scripts/")


def check_scripts(errors, plugin):
    node = shutil.which("node")
    for skill_dir in sorted(glob.glob(os.path.join(plugin, "skills", "*"))):
        scripts = os.path.join(skill_dir, "scripts")
        for path in sorted(glob.glob(os.path.join(scripts, "*.py"))):
            try:
                compile(open(path, encoding="utf-8").read(), path, "exec")
            except SyntaxError as e:
                errors.append(f"{rel(path)}: does not compile: {e}")
                continue
            if os.path.basename(path) == "relaylib.py":  # a library, not a command
                continue
            r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=60)
            if r.returncode == 0 or not (r.stdout + r.stderr).strip():
                errors.append(f"{rel(path)}: run without arguments should exit non-zero with usage")
        for path in sorted(glob.glob(os.path.join(scripts, "*.sh"))):
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            if r.returncode != 0:
                errors.append(f"{rel(path)}: bash -n failed: {r.stderr.strip()}")
        for path in sorted(glob.glob(os.path.join(scripts, "*.mjs"))) if node else []:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True)
            if r.returncode != 0:
                errors.append(f"{rel(path)}: node --check failed: {r.stderr.strip()}")
        template = os.path.join(scripts, "map_template.html")
        if os.path.exists(template):
            text = open(template, encoding="utf-8").read()
            for token in MAP_TOKENS:
                if token not in text:
                    errors.append(f"{rel(template)}: lacks {token}")


def plugin_files(plugin):
    files = {}
    for root, dirs, names in os.walk(plugin):
        dirs[:] = [d for d in dirs if d not in NOT_SHIPPED]
        for f in names:
            if f not in BUNDLE_IGNORE and not f.endswith(".plugin"):
                path = os.path.join(root, f)
                files[os.path.relpath(path, plugin).replace(os.sep, "/")] = path
    return files


def check_bundle(errors, plugin, name):
    """A <name>.plugin zip (the claude.ai upload) is optional; if one exists it must match the source."""
    bundle = os.path.join(ROOT, f"{name}.plugin")
    if not os.path.exists(bundle):
        return
    try:
        zf = zipfile.ZipFile(bundle)
    except zipfile.BadZipFile:
        errors.append(f"{rel(bundle)}: not a valid zip")
        return
    in_zip = {n for n in zf.namelist() if not n.endswith("/") and os.path.basename(n) not in BUNDLE_IGNORE}
    on_disk = plugin_files(plugin)
    stale = sorted(n for n in in_zip & on_disk.keys() if zf.read(n) != open(on_disk[n], "rb").read())
    for label, names in (("differs from source", stale), ("missing from bundle", sorted(on_disk.keys() - in_zip)),
                         ("only in bundle", sorted(in_zip - on_disk.keys()))):
        if names:
            errors.append(f"{rel(bundle)}: {label}: {', '.join(names[:5])}")


def main():
    errors = []
    plugin = plugin_dir(errors)
    if plugin is None:
        print(f"FAIL {errors[0]}")
        return 1
    skills = check_skills(errors, plugin)
    name = check_manifests(errors, plugin) or os.path.basename(plugin)
    commands = check_commands(errors, plugin, skills)
    check_links(errors, plugin, name, skills, commands)
    check_scripts(errors, plugin)
    check_bundle(errors, plugin, name)
    for e in errors:
        print(f"FAIL {e}")
    print(f"{name}: plugin valid ({len(skills)} skill(s), {len(commands)} command(s))" if not errors
          else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
