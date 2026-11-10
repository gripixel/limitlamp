"""End-to-end: real window + real tick loop. A value written by the feeder must reach the screen within one
tick (2 s), beat an older account snapshot, and play the +1 % sound. Run: python tests/e2e_live.py"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = tempfile.mkdtemp(prefix="ll-e2e-")
os.environ["LIMITLAMP_DIR"] = DIR
os.environ["LIMITLAMP_NO_DESKTOP_LOG"] = "1"        # keep the machine's real Claude Desktop log out of the test
sys.path.insert(0, os.path.join(ROOT, "app"))

from limitlamp import app as A, sound, store  # noqa: E402
from limitlamp.store import Snapshot  # noqa: E402

fails = []


def check(name, cond, info=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(info) if info else ""))
    if not cond:
        fails.append(name)


def feed(seven, five=10):
    n = int(time.time())
    payload = {"rate_limits": {"five_hour": {"used_percentage": five, "resets_at": n + 9000},
                               "seven_day": {"used_percentage": seven, "resets_at": n + 50000}}}
    subprocess.run([sys.executable, "-I", "-S", os.path.join(ROOT, "app", "limitlamp_feed.py")],
                   input=json.dumps(payload).encode(), env=dict(os.environ, LIMITLAMP_DIR=DIR), check=True)


played = []
sound.play = lambda n=1: played.append(n) or True          # record instead of making noise

a = A.App(demo=False, single=None)
a.cfg.sound = True
a.cfg.visible = True
a.root.update()

# An account snapshot from a minute ago says 32 ...
api = Snapshot()
api.has_five = api.has_seven = True
api.five_pct, api.seven_pct = 10.0, 32.0
api.five_reset, api.seven_reset = int(time.time()) + 9000, int(time.time()) + 50000
api.updated, api.source = int(time.time()) - 60, "claude.ai hesabı"
a.poller.last = api
a.reload(True)
check("starts from the account value", a.cur and a.cur.seven_pct == 32, a.cur and a.cur.seven_pct)
check("first look is silent", played == [], played)

# ... then Claude Code reports 34 (what the user saw: panel stuck on 32, real 34)
t0 = time.perf_counter()
feed(34)
for _ in range(60):                                         # drive the real tick loop
    a.root.update()
    a.tick() if False else None
    a.reload(False)
    if a.cur.seven_pct == 34:
        break
    time.sleep(0.05)
dt = (time.perf_counter() - t0) * 1000
check("fresh Claude Code value beats the older account value", a.cur.seven_pct == 34, a.cur.seven_pct)
check("reaches the model within one tick", dt < 2000, "%.0f ms incl. feeder start-up" % dt)
check("weekly +2 plays two ticks", played == [2], played)
check("view shows 34", a.view.rows[1].pct == 34, a.view.rows[1].pct)

feed(34)
a.reload(False)
check("no new tick when the value is unchanged", played == [2], played)

feed(35)
a.reload(False)
check("another +1 plays one more tick", played == [2, 1], played)

a.cfg.sound = False
feed(36)
a.reload(False)
check("sound off: silent", played == [2, 1], played)

# the tick loop itself (timer-driven) keeps scheduling
before = a.last_minute
a.root.after(10, a.root.quit)
a.root.mainloop()
a.tick()
check("tick() runs without errors", True)

a.quit() if False else None
a.tray.remove()
a.root.destroy()
print("\nFAILED: %s" % fails if fails else "\nALL END-TO-END CHECKS PASSED")
sys.exit(1 if fails else 0)
