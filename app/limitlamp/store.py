"""Shared data layer: limits.txt / history.csv in %LOCALAPPDATA%/LimitLamp (no network)."""
import os
import time

APP = "LimitLamp"
DIR = os.environ.get("LIMITLAMP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP)
LIMITS = os.path.join(DIR, "limits.txt")
HISTORY = os.path.join(DIR, "history.csv")


class ModelRow:
    def __init__(self, name="", pct=0.0, reset=0):
        self.name, self.pct, self.reset = name, pct, reset


class Snapshot:
    def __init__(self):
        self.has_five = self.has_seven = False
        self.five_pct = self.seven_pct = 0.0
        self.five_reset = self.seven_reset = 0     # epoch seconds, 0 = unknown
        self.updated = 0
        self.source = ""
        self.model = ""
        self.models = []                           # per-model weekly limits (account sign-in only)
        self.has_extra = False
        self.extra_pct = 0.0


def now():
    return int(time.time())


def _num(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


def write_limits(s):
    os.makedirs(DIR, exist_ok=True)
    lines = ["updated=%d" % s.updated]
    if s.has_five:
        lines += ["five_pct=" + _num(s.five_pct), "five_reset=%d" % s.five_reset]
    if s.has_seven:
        lines += ["seven_pct=" + _num(s.seven_pct), "seven_reset=%d" % s.seven_reset]
    lines.append("model=" + (s.model or "").replace("\r", " ").replace("\n", " "))
    tmp = "%s.%d.tmp" % (LIMITS, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.replace(tmp, LIMITS)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def read_limits():
    try:
        with open(LIMITS, "r", encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return None
    s = Snapshot()
    s.source = "Claude Code"
    for line in txt.split("\n"):
        k, sep, v = line.partition("=")
        if not sep:
            continue
        k, v = k.strip(), v.strip()
        try:
            if k == "updated":
                s.updated = int(v)
            elif k == "five_pct":
                s.five_pct, s.has_five = float(v), True
            elif k == "five_reset":
                s.five_reset = int(v)
            elif k == "seven_pct":
                s.seven_pct, s.has_seven = float(v), True
            elif k == "seven_reset":
                s.seven_reset = int(v)
            elif k == "model":
                s.model = v
        except ValueError:
            pass
    return s if s.updated > 0 else None


def append_history(s):
    os.makedirs(DIR, exist_ok=True)
    line = "%d,%s,%s\n" % (s.updated, _num(s.five_pct) if s.has_five else "",
                           _num(s.seven_pct) if s.has_seven else "")
    with open(HISTORY, "a", encoding="ascii") as f:
        f.write(line)


def read_history(since):
    """-> list of (t, five_pct|None, seven_pct|None)"""
    out = []
    try:
        with open(HISTORY, "r", encoding="ascii", errors="replace") as f:
            for line in f:
                p = line.strip().split(",")
                if len(p) < 3:
                    continue
                try:
                    t = int(p[0])
                    five = float(p[1]) if p[1] else None
                    seven = float(p[2]) if p[2] else None
                except ValueError:
                    continue
                if t >= since:
                    out.append((t, five, seven))
    except OSError:
        pass
    return out


def trim_history_if_big(limit=300 * 1024, keep_days=8):
    try:
        if os.path.getsize(HISTORY) < limit:
            return
        cutoff = now() - keep_days * 86400
        keep = []
        with open(HISTORY, "r", encoding="ascii", errors="replace") as f:
            for line in f:
                head = line.split(",", 1)[0]
                if head.isdigit() and int(head) >= cutoff:
                    keep.append(line)
        tmp = HISTORY + ".tmp"
        with open(tmp, "w", encoding="ascii") as f:
            f.writelines(keep)
        os.replace(tmp, HISTORY)
    except OSError:
        pass
