"""Shared helpers for the relay pipeline: project config, CSV inputs, FIT/GPX readers, a minimal FIT writer and
formatting. Every script takes the project directory (the folder holding event.json) as its first argument.

Project layout (see references/project-setup.md):
    event.json        event, team, timezone, official result, paths, map and flythrough options
    roster.csv        one row per leg: leg, runner, miles (+ optional van, rating, from_name, to_name, ...)
    recordings.csv    which source file covers which legs: file, legs ("5" or "22-23")
    gaps.csv          optional: unrecorded stretches to rebuild (reconstruct_gaps.py)
    places.csv        optional: place labels for the 3D flythrough (geocode_places.py)
    reconstructed/    rebuilt GPX files written by reconstruct_gaps.py (source files are never modified)
    cache/            network responses (routing, elevation, tiles, geocoding) so reruns stay offline
    out/              everything generated
"""
import csv, json, math, os, struct, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SEMI = 180 / 2**31
FIT_EPOCH = 631065600
GPX_NS = "{http://www.topografix.com/GPX/1/1}"
M_PER_MI = 1609.344
FT_PER_M = 3.28084

# Runner colors for dark imagery (flythrough), assigned by first appearance in leg order. Consecutive entries are
# spread so runners who hand off to each other stay distinguishable, including for color-vision deficiency.
FLY_PALETTE = ["#199e70", "#b24fc2", "#e66767", "#9085e9", "#a86a3a", "#1a9fbf", "#7d9a1e", "#3987e5",
               "#d55181", "#008300", "#5c6bc0", "#d95926", "#c98500"]


def die(msg):
    sys.exit(f"error: {msg}")


def _drop_empty(value):
    if isinstance(value, dict):
        return {k: _drop_empty(v) for k, v in value.items() if v not in ("", None) and not k.startswith("_")}
    return value


class Project:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        path = os.path.join(self.root, "event.json")
        if not os.path.exists(path):
            die(f"{path} not found; run new_project.py first")
        self.event = _drop_empty(json.load(open(path, encoding="utf-8")))  # "" and null mean "not given"
        for key in ("slug", "event", "team", "timezone"):
            if not self.event.get(key):
                die(f"event.json is missing '{key}'")
        self.slug = self.event["slug"]
        self.tz = ZoneInfo(self.event["timezone"])
        self.sources = self._dir(self.event.get("sources", "sources"))
        self.out = self._dir(self.event.get("output", "out"), make=True)
        self.cache = self._dir("cache", make=True)
        self.reconstructed = self._dir("reconstructed")

    def _dir(self, rel, make=False):
        p = os.path.normpath(os.path.join(self.root, rel))
        if make:
            os.makedirs(p, exist_ok=True)
        return p

    def file(self, name):
        return os.path.join(self.root, name)

    def output(self, suffix):  # out/<slug>_<suffix>
        return os.path.join(self.out, f"{self.slug}_{suffix}")

    def opt(self, *keys, default=None):
        d = self.event
        for k in keys:
            if not isinstance(d, dict) or k not in d:
                return default
            d = d[k]
        return d

    # ---------- inputs ----------
    def roster(self):
        rows = list(csv.DictReader(open(self.file("roster.csv"), encoding="utf-8")))
        for col in ("leg", "runner", "miles"):
            if rows and col not in rows[0]:
                die(f"roster.csv needs a '{col}' column")
        roster = {int(r["leg"]): r for r in rows}
        if sorted(roster) != list(range(1, len(roster) + 1)):
            die(f"roster.csv legs must be 1..N with no gaps, got {sorted(roster)}")
        return roster

    def gaps(self):
        path = self.file("gaps.csv")
        return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []

    def gap_output_name(self, gap):
        return gap.get("out") or f"{self.slug}_{gap['id']}_reconstructed.gpx"

    def recordings(self):
        """[(file, legs, start_utc, end_utc)] from recordings.csv, plus every gaps.csv reconstruction not already listed.
        start_utc / end_utc (optional, naive UTC datetimes) drop a recording's records before / after that moment:
        for a watch that ran along with a teammate on a leg someone else's recording covers, or kept running after."""
        rows = []
        for r in csv.DictReader(open(self.file("recordings.csv"), encoding="utf-8")):
            window = [utc_naive(r[k]) if (r.get(k) or "").strip() else None for k in ("start_utc", "end_utc")]
            rows.append((r["file"], parse_legs(r["legs"]), *window))
        listed = {row[0] for row in rows}
        for g in self.gaps():
            name = self.gap_output_name(g)
            if name not in listed:
                rows.append((name, parse_legs(g["legs"]), None, None))
        return rows

    def resolve(self, name):
        """A file named in recordings.csv: the project's reconstructed/ folder first, then the sources folder."""
        for base in (self.reconstructed, self.sources):
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
        die(f"{name} not found in {self.reconstructed} or {self.sources}")

    def manifest(self):
        return {int(r["leg"]): r for r in csv.DictReader(open(self.output("legs_manifest.csv"), encoding="utf-8"))}

    def merged_records(self):
        path = self.output("merged_records.csv")
        if not os.path.exists(path):
            die(f"{path} missing; run build_combined.py first")
        return list(csv.DictReader(open(path, encoding="utf-8")))

    def runner_order(self, roster=None):
        roster = roster or self.roster()
        return list(dict.fromkeys(roster[n]["runner"] for n in sorted(roster)))

    def fly_colors(self, roster=None):
        order = self.runner_order(roster)
        custom = self.opt("colors", default={}) or {}
        return {name: custom.get(name, FLY_PALETTE[i % len(FLY_PALETTE)]) for i, name in enumerate(order)}

    # ---------- time ----------
    def local(self, dt_utc):
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(self.tz)

    def clock(self, dt_utc):  # "Fri 6:00 AM"
        t = self.local(dt_utc)
        return f"{t:%a} {t.hour % 12 or 12}:{t:%M} {t:%p}"

    def tz_abbr(self, dt_utc):
        return self.local(dt_utc).strftime("%Z")


# ---------- small helpers ----------
def parse_legs(text):  # "22-23" -> [22, 23]
    first, _, last = str(text).strip().partition("-")
    return list(range(int(first), int(last or first) + 1))


def legs_label(ns):  # [2, 14, 25, 26] -> "2, 14, 25-26"
    groups = []
    for n in ns:
        if groups and n == groups[-1][-1] + 1:
            groups[-1].append(n)
        else:
            groups.append([n])
    return ", ".join(f"{g[0]}-{g[-1]}" if len(g) > 1 else str(g[0]) for g in groups)


def join_and(items):
    items = [str(i) for i in items]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def hmm(minutes):
    m = round(minutes)
    return f"{m // 60}:{m % 60:02d}"


def utc_naive(text):  # "2026-07-04 06:00:00" or ISO with Z -> naive UTC datetime
    return datetime.fromisoformat(text.replace("Z", "").replace("T", " "))


def haversine_deg(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def haversine_semi(a, b):
    return haversine_deg((a[0] * SEMI, a[1] * SEMI), (b[0] * SEMI, b[1] * SEMI))


def rebuilt_tag(recon_mi, total_mi):
    return "rebuilt" if recon_mi >= total_mi - 0.05 else "part rebuilt" if recon_mi > 0 else ""


def ascii_only(text, what):
    bad = sorted({c for c in text if ord(c) >= 128})
    if bad:
        die(f"{what} has non-ASCII characters {bad}; use plain ASCII names in roster.csv/event.json")


# ---------- readers: records use FIT semicircles for position, naive UTC datetimes ----------
def load_fit(path):
    import fitparse
    recs = []
    for m in fitparse.FitFile(path).get_messages("record"):
        v = m.get_values()
        if v.get("timestamp") is None:
            continue
        recs.append({
            "ts": v["timestamp"],
            "lat": v.get("position_lat"), "lon": v.get("position_long"),
            "alt": v.get("enhanced_altitude", v.get("altitude")),
            "spd": v.get("enhanced_speed", v.get("speed")),
            "hr": v.get("heart_rate"), "cad": v.get("cadence"),
            "pwr": v.get("power"), "dist": v.get("distance"),
        })
    return recs, "fit"


def _ext_int(ext, key):
    return int(float(ext[key])) if ext.get(key) else None


def load_gpx(path):
    root = ET.parse(path).getroot()
    about = " ".join(filter(None, [root.get("creator")] + [d.text for d in root.iter(GPX_NS + "desc")]))
    recs, dist, prev = [], 0.0, None
    for pt in root.iter(GPX_NS + "trkpt"):
        t = pt.find(GPX_NS + "time")
        if t is None:
            continue
        pos = (round(float(pt.get("lat")) / SEMI), round(float(pt.get("lon")) / SEMI))
        if prev:
            dist += haversine_semi(prev, pos)
        prev = pos
        ele = pt.find(GPX_NS + "ele")
        ext = {e.tag.rsplit("}", 1)[-1]: e.text for e in pt.iter()}  # hr, cad, power from any extension namespace
        ts = datetime.fromisoformat(t.text.replace("Z", "+00:00")).astimezone(timezone.utc)
        recs.append({"ts": ts.replace(tzinfo=None, microsecond=0), "lat": pos[0], "lon": pos[1],
                     "alt": float(ele.text) if ele is not None else None, "spd": None,
                     "hr": _ext_int(ext, "hr"), "cad": _ext_int(ext, "cad"), "pwr": _ext_int(ext, "power"), "dist": dist})
    return recs, "reconstructed" if "reconstructed" in about.lower() else "gpx"


def load_any(path):
    return (load_gpx if path.lower().endswith(".gpx") else load_fit)(path)


def gps_fixes(path, start_utc=None, end_utc=None):
    """[(naive UTC ts, (lat, lon) degrees, altitude m or None)] with a position, time-ordered, inside the window."""
    recs, _ = load_any(path)
    out = [(r["ts"], (r["lat"] * SEMI, r["lon"] * SEMI), r["alt"]) for r in recs if r["lat"] is not None
           and (start_utc is None or r["ts"] >= start_utc) and (end_utc is None or r["ts"] <= end_utc)]
    return sorted(out, key=lambda x: x[0])


# ---------- FIT writer ----------
ENUM, UINT8, UINT16, SINT32, UINT32, UINT32Z = (0x00, 1, 0xFF), (0x02, 1, 0xFF), (0x84, 2, 0xFFFF), \
    (0x85, 4, 0x7FFFFFFF), (0x86, 4, 0xFFFFFFFF), (0x8C, 4, 0)
_FMT = {0x00: "B", 0x02: "B", 0x84: "H", 0x85: "i", 0x86: "I", 0x8C: "I"}
_CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def fit_ts(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) - FIT_EPOCH


def crc16(data, crc=0):
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]; crc = (crc >> 4) & 0x0FFF; crc ^= tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]; crc = (crc >> 4) & 0x0FFF; crc ^= tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


class FitWriter:
    def __init__(self):
        self.body = bytearray()
        self.defs = {}  # global msg num -> (local id, fields)

    def define(self, local, gmsg, fields):
        self.defs[gmsg] = (local, fields)
        self.body += struct.pack("<BBBHB", 0x40 | local, 0, 0, gmsg, len(fields))
        for num, (bt, size, _inv) in fields:
            self.body += struct.pack("<BBB", num, size, bt)

    def write(self, gmsg, values):
        local, fields = self.defs[gmsg]
        self.body += struct.pack("<B", local)
        for num, (bt, size, inv) in fields:
            v = values.get(num)
            self.body += struct.pack("<" + _FMT[bt], inv if v is None else int(round(v)))

    def bytes(self):
        header = struct.pack("<BBHI4s", 14, 0x20, 2134, len(self.body), b".FIT")
        header += struct.pack("<H", crc16(header))
        out = header + bytes(self.body)
        return out + struct.pack("<H", crc16(out))


def scaled(v, scale=1, offset=0):
    return None if v is None else (v + offset) * scale


# ---------- sun (NOAA solar position algorithm) ----------
def sun_events(lat, lon, day):
    """(sunrise, sunset) as naive UTC datetimes for a calendar date at lat/lon, or None in polar day/night."""
    jd = datetime(day.year, day.month, day.day, 12).toordinal() + 1721424.5
    out = []
    for rising in (True, False):
        t = jd - 0.5 + (0.25 if rising else 0.75) - lon / 360  # first guess, then iterate
        for _ in range(3):
            jc = (t - 2451545.0) / 36525
            L0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
            M = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
            e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
            C = (math.sin(math.radians(M)) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
                 + math.sin(math.radians(2 * M)) * (0.019993 - 0.000101 * jc) + math.sin(math.radians(3 * M)) * 0.000289)
            omega = 125.04 - 1934.136 * jc
            lam = L0 + C - 0.00569 - 0.00478 * math.sin(math.radians(omega))
            eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
            eps = eps0 + 0.00256 * math.cos(math.radians(omega))
            decl = math.degrees(math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam))))
            y = math.tan(math.radians(eps / 2)) ** 2
            eqt = 4 * math.degrees(y * math.sin(2 * math.radians(L0)) - 2 * e * math.sin(math.radians(M))
                                   + 4 * e * y * math.sin(math.radians(M)) * math.cos(2 * math.radians(L0))
                                   - 0.5 * y * y * math.sin(4 * math.radians(L0)) - 1.25 * e * e * math.sin(2 * math.radians(M)))
            cos_ha = (math.cos(math.radians(90.833)) / (math.cos(math.radians(lat)) * math.cos(math.radians(decl)))
                      - math.tan(math.radians(lat)) * math.tan(math.radians(decl)))
            if abs(cos_ha) > 1:
                return None
            ha = math.degrees(math.acos(cos_ha)) * (1 if rising else -1)
            minutes = 720 - 4 * (lon + ha) - eqt  # UTC minutes from midnight
            t = jd - 0.5 + minutes / 1440
        out.append(datetime(day.year, day.month, day.day) + timedelta(minutes=minutes))
    return tuple(out)


def nights_between(lat, lon, t0, t1):
    """[(sunset, sunrise)] naive UTC intervals of darkness overlapping [t0, t1]."""
    events = []
    d = (t0 - timedelta(days=1)).date()
    while d <= (t1 + timedelta(days=1)).date():
        ev = sun_events(lat, lon, d)
        if ev:
            events += [("rise", ev[0]), ("set", ev[1])]
        d += timedelta(days=1)
    events.sort(key=lambda e: e[1])
    nights = []
    for (k1, a), (k2, b) in zip(events, events[1:]):
        if k1 == "set" and k2 == "rise" and b > t0 and a < t1:
            nights.append((a, b))
    return nights
