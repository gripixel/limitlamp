"""limitlamp_feed.py - runs as Claude Code's `statusLine` command.

Claude Code passes the session JSON on stdin; for subscribers it contains an official
`rate_limits` field (5-hour + 7-day usage percentage and reset time). Only those limits are written
to %LOCALAPPDATA%/LimitLamp (limits.txt, history.csv). Project paths, conversation content and tokens
are never stored. No network. Self-contained so it starts fast (run it with `python -I -S`).
"""
import json
import os
import sys
import time

DIR = os.environ.get("LIMITLAMP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "LimitLamp")
LIMITS = os.path.join(DIR, "limits.txt")
HISTORY = os.path.join(DIR, "history.csv")


def num(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


def window(rl, key):
    w = rl.get(key)
    if not isinstance(w, dict) or not isinstance(w.get("used_percentage"), (int, float)):
        return None
    r = w.get("resets_at")
    return float(w["used_percentage"]), int(r) if isinstance(r, (int, float)) else 0


def read_old():
    old = {}
    try:
        with open(LIMITS, "r", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                old[k] = v
    except OSError:
        pass
    return old


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace") or "{}")
        rl = data.get("rate_limits") if isinstance(data, dict) else None
        if not isinstance(rl, dict):
            return 0                                   # not a subscriber / no response yet
        five, seven = window(rl, "five_hour"), window(rl, "seven_day")
        model = ""
        m = data.get("model")
        if isinstance(m, dict) and isinstance(m.get("display_name"), str):
            model = m["display_name"].replace("\n", " ").replace("\r", " ")
        now = int(time.time())

        lines = ["updated=%d" % now]
        if five:
            lines += ["five_pct=" + num(five[0]), "five_reset=%d" % five[1]]
        if seven:
            lines += ["seven_pct=" + num(seven[0]), "seven_reset=%d" % seven[1]]
        lines.append("model=" + model)

        old = read_old()
        new_map = dict(l.split("=", 1) for l in lines)
        keys = ("five_pct", "five_reset", "seven_pct", "seven_reset")
        changed = any(old.get(k) != new_map.get(k) for k in keys)
        try:
            age = now - int(old.get("updated", "0"))
        except ValueError:
            age = 10 ** 9

        # Nothing changed and the data is fresh: don't touch the disk (statusLine fires often).
        if changed or age >= 60:
            os.makedirs(DIR, exist_ok=True)
            tmp = "%s.%d.tmp" % (LIMITS, os.getpid())
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp, LIMITS)
            if changed or age >= 600:
                with open(HISTORY, "a", encoding="ascii") as f:
                    f.write("%d,%s,%s\n" % (now, num(five[0]) if five else "", num(seven[0]) if seven else ""))
                try:
                    if os.path.getsize(HISTORY) > 300 * 1024:
                        cutoff = now - 8 * 86400
                        with open(HISTORY, "r", encoding="ascii", errors="replace") as f:
                            keep = [l for l in f if l.split(",", 1)[0].isdigit() and int(l.split(",", 1)[0]) >= cutoff]
                        with open(HISTORY + ".tmp", "w", encoding="ascii") as f:
                            f.writelines(keep)
                        os.replace(HISTORY + ".tmp", HISTORY)
                except OSError:
                    pass

        out = []
        if five:
            out.append("5h %d%%" % round(five[0]))
        if seven:
            out.append("7d %d%%" % round(seven[0]))
        sys.stdout.write(" | ".join(out))
    except Exception:
        if os.environ.get("LIMITLAMP_DEBUG") == "1":
            import traceback
            traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
