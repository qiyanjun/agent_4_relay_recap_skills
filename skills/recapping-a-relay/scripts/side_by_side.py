"""Join the 2D and 3D flythroughs into one side-by-side video under a header that names each half.

Usage: python3 side_by_side.py <project_dir> [preview]
Inputs: out/<slug>_flythrough_2d.mp4 and out/<slug>_flythrough.mp4 (or their _preview versions) and their
_timeline.json files, all written by render_flythrough.sh. Output: out/<slug>_flythrough_2d3d.mp4 (2D on the left).

Both halves come from the same scene and the same clock, so they are joined frame for frame; the timelines are
compared first and a mismatch (say, one half rendered before a roster fix) stops it rather than drift out of step.
event.json "flythrough": {"panel_titles": [["2D title", "subtitle"], ["3D title", "subtitle"]]} replaces the header.
"""
import json, os, shutil, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont
from relaylib import Project, die

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if not args:
    sys.exit(__doc__)
P = Project(args[0])
tag = "_preview" if len(args) > 1 and args[1] == "preview" else ""
if not shutil.which("ffmpeg"):
    die("ffmpeg not found")
left, right = P.output(f"flythrough_2d{tag}.mp4"), P.output(f"flythrough{tag}.mp4")
for path in (left, right):
    if not os.path.exists(path):
        die(f"{path} not found; render both views first (flythrough.side_by_side, or VIEW=2d render_flythrough.sh)")
tl = [json.load(open(p[:-4] + "_timeline.json")) for p in (left, right) if os.path.exists(p[:-4] + "_timeline.json")]
if len(tl) == 2:
    strip = lambda t: [(L["leg"], L["runner"], L["start_frame"], L["end_frame"]) for L in t["legs"]]
    if tl[0]["frames"] != tl[1]["frames"] or strip(tl[0]) != strip(tl[1]):
        die("the 2D and 3D renders are on different timelines or rosters; re-render both from the current scene")
    W, H = tl[1]["width"], tl[1]["height"]
else:
    print("note: no timelines to compare; joining as they are")
    W, H = 1080, 1920

miles = sum(float(r["miles"]) for r in P.roster().values())
titles = P.opt("flythrough", "panel_titles") or [
    ["2D Satellite Map", "Top-down view of each leg as it was run"],
    ["3D Terrain Flythrough", f"The mountains and towns along all {miles:.1f} miles"],
]
if len(titles) != 2 or any(len(t) != 2 for t in titles):
    die("flythrough.panel_titles must be two [title, subtitle] pairs: the 2D half, then the 3D half")


def font(bold, size):
    for path, index in (("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0),
                        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf", 0),
                        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
                        ("C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf", 0)):
        if os.path.exists(path):
            return ImageFont.truetype(path, size, index=index)
    return ImageFont.load_default(size)


HEAD = round(H / 16)  # 120 px over a 1920 px panel
header = Image.new("RGB", (2 * W, HEAD), (8, 10, 10))
draw = ImageDraw.Draw(header)
big, small = font(True, max(8, round(HEAD * 0.37))), font(False, max(8, round(HEAD * 0.22)))
for k, (title, sub) in enumerate(titles):
    cx = W * k + W / 2
    draw.text((cx, HEAD * 0.40), title, font=big, fill=(255, 255, 255), anchor="mm")
    draw.text((cx, HEAD * 0.76), sub, font=small, fill=(175, 182, 179), anchor="mm")
draw.line([(W, HEAD * 0.18), (W, HEAD * 0.86)], fill=(60, 66, 63), width=2)

out = P.output(f"flythrough_2d3d{tag}.mp4")
with tempfile.TemporaryDirectory() as tmp:
    png = os.path.join(tmp, "header.png")
    header.save(png)
    # pad + overlay rather than vstack: the still header then holds over every frame at the videos' own rate
    subprocess.run(["ffmpeg", "-v", "error", "-stats", "-y", "-i", left, "-i", right, "-i", png,
                    "-filter_complex", f"[0:v][1:v]hstack=inputs=2[m];[m]pad=iw:ih+{HEAD}:0:{HEAD}[p];"
                                       "[p][2:v]overlay=0:0,format=yuv420p[v]",
                    "-map", "[v]", "-c:v", "libx264", "-crf", os.environ.get("CRF", "22"),
                    "-preset", "medium", "-movflags", "+faststart", out], check=True)
print(f"{out}: {2 * W}x{H + HEAD}, {os.path.getsize(out) / 1e6:.0f} MB")
