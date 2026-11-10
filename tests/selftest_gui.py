"""GUI self-test (runs a real layered window in demo mode, drives it through its own handlers).
Run:  pythonw/python tests/selftest_gui.py"""
import os, sys, tempfile, time
os.environ["LIMITLAMP_DIR"] = tempfile.mkdtemp(prefix="ll-gui-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import ctypes
from ctypes import wintypes as wt
from limitlamp import app as A, render, winapi, model, store

fails = []
def check(name, cond, info=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(info) if info else ""))
    if not cond: fails.append(name)

def rect(hwnd):
    r = wt.RECT(); ctypes.windll.user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top

class Ev:
    def __init__(s, x=0, y=0, rx=0, ry=0, state=0, delta=0): s.x, s.y, s.x_root, s.y_root, s.state, s.delta = x, y, rx, ry, state, delta

a = A.App(demo=True)
r = a.root
r.update()
x0, y0, w0, h0 = rect(a.hwnd)
check("window created, layered+visible", ctypes.windll.user32.IsWindowVisible(wt.HWND(a.hwnd)) and w0 > 300, (w0, h0))
ex = ctypes.windll.user32.GetWindowLongPtrW(wt.HWND(a.hwnd), -20)
check("ex-style layered+toolwindow", ex & 0x80000 and ex & 0x80, hex(ex))

t = time.perf_counter(); a.redraw(); dt = (time.perf_counter() - t) * 1000
print("      full-quality redraw: %.0f ms" % dt)
t = time.perf_counter(); a.redraw(fast=True); print("      fast redraw: %.0f ms" % ((time.perf_counter() - t) * 1000))

# zoom
bottom0 = y0 + h0
a.set_zoom(170); r.update()
x1, y1, w1, h1 = rect(a.hwnd)
check("zoom 170% scales window", abs(w1 / w0 - 1.7) < 0.03 and abs(h1 / h0 - 1.7) < 0.03, (w1, h1))
check("zoom keeps bottom anchored", abs((y1 + h1) - bottom0) <= 2, (y1 + h1, bottom0))
a.set_zoom(100); r.update()

# compact toggle
_, y2, w2, h2 = rect(a.hwnd)
bottom2 = y2 + h2
a.set_compact(True); r.update()
_, y3, w3, h3 = rect(a.hwnd)
check("compact shrinks height, same width", h3 < h2 * 0.4 and w3 == w2, (w3, h3))
check("compact keeps bottom anchored", abs((y3 + h3) - bottom2) <= 2)
a.set_compact(False); r.update()

# resize from the bottom-right edge (aspect locked)
x4, y4, w4, h4 = rect(a.hwnd)
sc = a.scale
ph = render.panel_height(a.view, False)
ex_, ey_ = (render.M + render.PW + 4) * sc, (render.M + ph + 4) * sc
a.on_press(Ev(ex_, ey_, x4 + ex_, y4 + ey_))
check("edge press starts resize", a.mode and a.mode[0] == "resize" and a.mode[1] == "br", a.mode)
a.on_drag(Ev(ex_, ey_, x4 + ex_ + 120, y4 + ey_ + 10)); r.update(); time.sleep(0.2); r.update()
a.on_release(Ev())
x5, y5, w5, h5 = rect(a.hwnd)
check("resize grows width", w5 > w4 + 100, (w4, w5))
check("resize keeps aspect ratio", abs(h5 / w5 - h4 / w4) < 0.004, (h5 / w5, h4 / w4))
check("resize saved as zoom", a.cfg.zoom > 100, a.cfg.zoom)
check("content scale follows window", abs(a.scale - w5 / render.WT) < 0.01)

# drag to move
a.set_zoom(100); r.update(); x6, y6, w6, h6 = rect(a.hwnd)
a.on_press(Ev(40 * a.scale, 120 * a.scale, x6 + 40, y6 + 120))
a.on_drag(Ev(40 * a.scale, 120 * a.scale, x6 + 40 + 50, y6 + 120 - 30)); r.update()
a.on_release(Ev()); x7, y7, _, _ = rect(a.hwnd)
check("drag moves window", (x7 - x6, y7 - y6) == (50, -30), (x7 - x6, y7 - y6))

# ctrl+wheel zoom
z = a.cfg.zoom
a.on_wheel(Ev(state=0x4, delta=120)); r.update()
check("ctrl+wheel zooms", a.cfg.zoom > z, (z, a.cfg.zoom))
z = a.cfg.zoom
a.on_wheel(Ev(state=0, delta=120))
check("wheel without ctrl does nothing", a.cfg.zoom == z)

# hit areas
bx, by, bw, bh = a.hits["menu"]
check("menu button hit-test", a.hot(bx + 3, by + 3) == "menu")
check("close button hit-test", a.hot(a.hits["close"][0] + 3, a.hits["close"][1] + 3) == "close")

# hide / show
a.hide(); r.update()
check("hide hides window", not ctypes.windll.user32.IsWindowVisible(wt.HWND(a.hwnd)))
a.show(); r.update()
check("show shows window again", bool(ctypes.windll.user32.IsWindowVisible(wt.HWND(a.hwnd))))

# opacity affects background only (render-level)
a.set_opacity(50); a.set_opacity(88)

# no-data view (CTA) and menu / dialog construction
a.cur = None; a.view = model.build_view(None, [], False, "", store.now()); a.apply_layout(True); r.update()
check("no-data CTA hit area present", "cta" in a.hits)
a.demo = False  # allow signed-out dialog flow
d = A.SignInDialog(a); r.update()
check("sign-in dialog opens", d.top.winfo_exists())
check("sign-in url has PKCE", "code_challenge=" in A.auth.build_authorize_url(d.challenge, d.state))
d.var.set("bogus#" + "x"); d.connect()      # wrong state -> error shown, no crash
for _ in range(30):
    r.update(); time.sleep(0.05)
check("sign-in shows an error for a bad code", "state" in d.err.cget("text") or "HTTP" in d.err.cget("text"), d.err.cget("text")[:60])
d.close()
a.demo = True
m = a.make_menu()
labels = [m.entrycget(i, "label") for i in range(m.index("end") + 1) if m.type(i) in ("command", "checkbutton", "cascade")]
check("menu has expected items", all(x in labels for x in ("Yenile", "Kompakt görünüm", "Boyut", "Çıkış")), labels)

print("\nFAILED: %s" % fails if fails else "\nALL GUI CHECKS PASSED")
r.destroy()
sys.exit(1 if fails else 0)
