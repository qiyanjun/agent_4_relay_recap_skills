"""Lay a background track over the finished flythrough, and give both files a cover image.

Usage: python3 add_music.py <project_dir> [--music FILE] [--poster SECONDS] [--volume 1.0]
Reads event.json's "flythrough": {"music": "song.mp3"} (relative to the project) unless --music is given.
Writes out/<slug>_flythrough_music.mp4 beside the silent out/<slug>_flythrough.mp4.

Two things this gets right, both of which are easy to get wrong by hand:

**The track is trimmed where nobody is singing.** A song is almost never the video's length. Cutting the tail
chops the last chorus off mid-word; cutting the head loses the intro but is inaudible as a loss. So the window is
anchored on its END - at the track's last audible moment, found here by reading the loudness profile - and the
start is whatever the video's length asks for. A pop song's intro is instrumental, so the trim lands there, the
song's own ending falls on the video's last frame, and nothing sung is lost. If the video is longer than the
track, the track is padded with silence instead and the fade-out covers it.

**The first frame is not the thumbnail you want.** A player shows frame 0 while a video sits unplayed, and an
opening title page whose lines animate in has almost nothing on frame 0. This attaches the frame at --poster
(default 2.7 s, where a title page is usually fully composed) as MP4 cover art, which is what Finder and media
libraries use. Players that always draw frame 0 still show frame 0 - the page itself has to be composed there,
which is what the flythrough's title page does.

The video is stream-copied throughout, so only the audio is encoded and the whole step takes seconds.
"""
import argparse, math, os, shutil, struct, subprocess, sys, tempfile
from relaylib import Project, die

ap = argparse.ArgumentParser()
ap.add_argument("project")
ap.add_argument("--music", help="audio file; default event.json flythrough.music")
ap.add_argument("--poster", type=float, default=2.7, help="seconds to take the cover image from (default 2.7)")
ap.add_argument("--volume", type=float, default=1.0)
ap.add_argument("--fade-in", type=float, default=1.5)
ap.add_argument("--fade-out", type=float, default=2.0)
ap.add_argument("--start", type=float, help="force the track's start offset instead of anchoring on its end")
a = ap.parse_args()

P = Project(a.project)
for tool in ("ffmpeg", "ffprobe"):
    if not shutil.which(tool):
        die(f"{tool} not found")
video = P.output("flythrough.mp4")
if not os.path.exists(video):
    die(f"{video} not found; render the flythrough first")
music = a.music or P.opt("flythrough", "music")
if not music:
    die('no music: pass --music FILE or set "music" in event.json\'s "flythrough"')
music = music if os.path.isabs(music) else os.path.join(P.root, music)
if not os.path.exists(music):
    die(f"{music} not found")
out = P.output("flythrough_music.mp4")


def probe(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip())


def last_audible(path, window=0.5, floor_db=-35):
    """The moment the track stops making a sound, from its loudness profile (mono 8 kHz), so the trim can be
    anchored there rather than on a number somebody measured by ear once."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    count = len(raw) // 2
    if not count:
        return probe(path)
    samples = struct.unpack(f"<{count}h", raw[:count * 2])
    step, peak, bands = int(8000 * window), 1.0, []
    for i in range(0, count - step, step):
        chunk = samples[i:i + step]
        rms = math.sqrt(sum(x * x for x in chunk) / step) or 1
        db = 20 * math.log10(rms / 32768)
        peak = max(peak, db) if bands else db
        bands.append((i / 8000, db))
    peak = max(db for _, db in bands)
    audible = [t for t, db in bands if db - peak > floor_db]
    return (audible[-1] + window) if audible else probe(path)


dur, track = probe(video), probe(music)
end = last_audible(music)
start = a.start if a.start is not None else max(0.0, round(end - dur, 3))
if a.start is None and end - dur < 0:
    print(f"note: the track's {end:.1f} s of sound is shorter than the {dur:.1f} s video; "
          "it is padded with silence at the end")
print(f"{os.path.basename(video)} {dur:.1f}s + {os.path.basename(music)} "
      f"({track:.1f}s, last sound at {end:.1f}s) -> using {start:.1f}-{round(start + dur, 1)}s")

# apad first, so a video longer than the remaining track still gets a full-length audio stream
fade_out_at = max(0.0, dur - a.fade_out)
subprocess.run(["ffmpeg", "-v", "error", "-stats", "-y", "-i", video, "-i", music, "-filter_complex",
                f"[1:a]apad,atrim={start}:{round(start + dur, 3)},asetpts=N/SR/TB,volume={a.volume},"
                f"afade=t=in:st=0:d={a.fade_in},afade=t=out:st={fade_out_at}:d={a.fade_out}[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart", out], check=True)


def set_poster(path, at):
    """Attach the frame at `at` as MP4 cover art, stream-copying everything else."""
    with tempfile.TemporaryDirectory() as tmp:
        png, mp4 = os.path.join(tmp, "poster.png"), os.path.join(tmp, "out.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", path,
                        "-frames:v", "1", "-update", "1", png], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-i", png, "-map", "0", "-map", "1",
                        "-c", "copy", "-c:v:1", "png", "-disposition:v:1", "attached_pic",
                        "-movflags", "+faststart", mp4], check=True)
        shutil.move(mp4, path)


for path in (video, out):
    set_poster(path, min(a.poster, dur - 0.1))
print(f"{out}: {os.path.getsize(out) / 1e6:.0f} MB, {dur:.0f} s, cover image from {a.poster} s "
      f"(also set on {os.path.basename(video)})")
