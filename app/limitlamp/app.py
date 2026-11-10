"""Limit Lamp application: Tk event loop + a per-pixel-alpha layered window drawn with Pillow."""
import copy
import json
import os
import queue
import re
import sys
import threading
import time
import webbrowser

from . import auth, model, render, sound, store, winapi
from .render import M, WT
from .store import Snapshot

ZOOMS = (75, 100, 130, 170)
OPACITIES = (100, 90, 80, 65, 50)
INTERVALS = ((30, "30 sn"), (60, "1 dk"), (120, "2 dk"))
VOLUMES = ((35, "Hafif"), (60, "Orta"), (90, "Yüksek"))
CURSORS = {"l": "size_we", "r": "size_we", "t": "size_ns", "b": "size_ns",
           "tl": "size_nw_se", "br": "size_nw_se", "tr": "size_ne_sw", "bl": "size_ne_sw"}
BG, BG2, FG, MUTED_HEX, ACCENT_HEX = "#181b23", "#22262f", "#eef0f5", "#929aa8", "#d97757"


class Cfg:
    FILE = "settings.json"

    def __init__(self):
        self.x = self.y = None
        self.top, self.compact, self.visible = True, False, True
        self.opacity, self.zoom = 88, 100
        self.sound, self.interval = True, 60      # tick on weekly +1 % / account poll period (s)
        self.volume = sound.DEFAULT_VOLUME        # tick loudness (%)

    @classmethod
    def load(cls):
        c = cls()
        try:
            with open(os.path.join(store.DIR, cls.FILE), "r", encoding="utf-8") as f:
                d = json.load(f)
            for k in ("x", "y", "top", "compact", "visible", "opacity", "zoom", "sound", "interval", "volume"):
                if k in d:
                    setattr(c, k, d[k])
            c.opacity = max(30, min(100, int(c.opacity)))
            c.zoom = max(50, min(300, int(c.zoom)))
            c.interval = max(20, min(600, int(c.interval)))
            c.sound = bool(c.sound)
            c.volume = max(10, min(100, int(c.volume)))
        except (OSError, ValueError, TypeError):
            pass
        return c

    def save(self):
        try:
            os.makedirs(store.DIR, exist_ok=True)
            with open(os.path.join(store.DIR, self.FILE), "w", encoding="utf-8") as f:
                json.dump(self.__dict__, f)
        except OSError:
            pass


# ------------------------------------------------------------------ Desktop log (fallback source)

_RX_SAMPLE = re.compile(r'"t":(\d+),"org":"[^"]*","u":\{([^}]*)\}')
_RX_FH = re.compile(r'"fh":(\d+(?:\.\d+)?)')
_RX_SD = re.compile(r'"sd":(\d+(?:\.\d+)?)')


def find_desktop_log():
    if os.environ.get("LIMITLAMP_NO_DESKTOP_LOG") == "1":       # tests / opt-out
        return None
    local = os.environ.get("LOCALAPPDATA") or ""
    cd = os.path.join(local, "Claude-Data")
    try:
        for d in os.listdir(cd):
            p = os.path.join(cd, d, "plan-usage-history.json")
            if os.path.isfile(p):
                return p
    except OSError:
        pass
    p = os.path.join(os.environ.get("APPDATA") or "", "Claude", "plan-usage-history.json")
    return p if os.path.isfile(p) else None


def read_desktop_log(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return None
    best = None
    for m in _RX_SAMPLE.finditer(txt):
        u = m.group(2)
        f, d = _RX_FH.search(u), _RX_SD.search(u)
        if not f and not d:
            continue
        t = int(m.group(1)) // 1000
        if best is not None and t < best.updated:
            continue
        s = Snapshot()
        s.updated, s.source = t, "Claude Desktop günlüğü"
        if f:
            s.has_five, s.five_pct = True, float(f.group(1))
        if d:
            s.has_seven, s.seven_pct = True, float(d.group(1))
        best = s
    return best


def merge(feed, log):
    if feed is None:
        return log
    if log is None or feed.updated >= log.updated:
        return feed
    now = store.now()
    m = Snapshot()
    m.updated, m.source = log.updated, log.source
    m.has_five, m.five_pct, m.has_seven, m.seven_pct = log.has_five, log.five_pct, log.has_seven, log.seven_pct
    m.five_reset = feed.five_reset if feed.five_reset > now else 0
    m.seven_reset = feed.seven_reset if feed.seven_reset > now else 0
    return m


def choose(api, best, now):
    """The freshest snapshot wins. If it is not the account one but the account data is recent,
    keep the account-only extras (per-model limits, extra usage) so they do not flicker away."""
    cands = [c for c in (api, best) if c is not None]
    if not cands:
        return None
    pick = max(cands, key=lambda c: c.updated)
    if pick is not api and api is not None and now - api.updated < 20 * 60 and (api.models or api.has_extra):
        pick = copy.copy(pick)
        pick.models, pick.has_extra, pick.extra_pct = api.models, api.has_extra, api.extra_pct
    return pick


class ActivityWatcher:
    """Notices Claude Code activity (every surface writes ~/.claude/projects/**/*.jsonl as it works) so the
    account poll can run right after a response instead of waiting for the next interval. Reads no content."""

    def __init__(self, root=None):
        self.root = root or os.path.join(os.path.expanduser("~"), ".claude", "projects")
        self.tracked = {}
        self.last_scan = 0.0
        self.ready = False

    def _scan(self, now):
        cutoff, found = now - 15 * 60, {}
        try:
            for d in os.scandir(self.root):
                if not d.is_dir():
                    continue
                for f in os.scandir(d.path):
                    if f.name.endswith(".jsonl"):
                        st = f.stat()
                        if st.st_mtime >= cutoff:
                            found[f.path] = (st.st_mtime, st.st_size)
        except OSError:
            pass
        return found

    def changed(self, now=None):
        now = time.time() if now is None else now
        hit = False
        if now - self.last_scan >= 30:                  # full rescan: also finds brand-new sessions
            self.last_scan = now
            new = self._scan(now)
            if self.ready:
                hit = any(self.tracked.get(p) != v for p, v in new.items())
            self.tracked, self.ready = new, True
            return hit
        for p, v in list(self.tracked.items()):         # cheap: stat only the recently active files
            try:
                st = os.stat(p)
            except OSError:
                continue
            cur = (st.st_mtime, st.st_size)
            if cur != v:
                self.tracked[p] = cur
                hit = True
        return hit


# ------------------------------------------------------------------ sign-in dialog

class SignInDialog:
    def __init__(self, app):
        import tkinter as tk
        self.app, self.tk = app, tk
        self.verifier, self.challenge, self.state = auth.new_pkce()
        u = app.dpi / 96.0
        t = self.top = tk.Toplevel(app.root)
        t.title("Limit Lamp · Claude hesabını bağla")
        t.configure(bg=BG)
        t.resizable(False, False)
        t.attributes("-topmost", True)
        W, H = int(500 * u), int(392 * u)
        t.geometry("%dx%d+%d+%d" % (W, H, (t.winfo_screenwidth() - W) // 2, (t.winfo_screenheight() - H) // 3))
        pad = int(24 * u)
        tk.Label(t, text="Claude hesabını bağla", bg=BG, fg=FG, font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=pad, pady=(pad * 3 // 4, 6))
        tk.Label(t, justify="left", anchor="w", bg=BG, fg=FG, font=("Segoe UI", 10), wraplength=W - 2 * pad,
                 text="Limit Lamp, kullanımını doğrudan hesabından okuyabilsin diye giriş ister.\n\n"
                      "1)  “Tarayıcıda aç”a tıkla; Claude'da giriş yap ve izin ver.\n"
                      "2)  Açılan sayfadaki kodu kopyala.\n"
                      "3)  Aşağıya yapıştır ve “Bağlan”a bas.").pack(anchor="w", padx=pad)
        self.btn_open = self._btn(t, "Tarayıcıda aç", True, self.open_browser)
        self.btn_open.pack(anchor="w", padx=pad, pady=(10, 8), ipadx=10, ipady=3)
        self.var = tk.StringVar()
        self.entry = tk.Entry(t, textvariable=self.var, bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                              font=("Consolas", 10), highlightthickness=1, highlightbackground="#3a404c", highlightcolor=ACCENT_HEX)
        self.entry.pack(fill="x", padx=pad, ipady=5)
        self.err = tk.Label(t, text="", bg=BG, fg="#f87171", font=("Segoe UI", 9), anchor="w", justify="left", wraplength=W - 2 * pad)
        self.err.pack(fill="x", padx=pad, pady=(6, 0))
        tk.Label(t, bg=BG, fg=MUTED_HEX, font=("Segoe UI", 8), justify="left", anchor="w", wraplength=W - 2 * pad,
                 text="Yalnızca kullanım bilgisini okuma izni (user:profile) istenir. Şifren bu programa girilmez. "
                      "Oturum anahtarı bu bilgisayarda Windows DPAPI ile şifrelenerek saklanır; istediğin an menüden silebilirsin."
                 ).pack(fill="x", padx=pad, pady=(6, 0))
        bar = tk.Frame(t, bg=BG)
        bar.pack(side="bottom", fill="x", padx=pad, pady=pad * 2 // 3)
        self.btn_go = self._btn(bar, "Bağlan", True, self.connect)
        self.btn_go.pack(side="right", ipadx=12, ipady=3)
        self._btn(bar, "İptal", False, self.close).pack(side="right", padx=8, ipadx=10, ipady=3)
        t.bind("<Return>", lambda e: self.connect())
        t.bind("<Escape>", lambda e: self.close())
        t.protocol("WM_DELETE_WINDOW", self.close)
        self.q = queue.Queue()
        self.entry.focus_set()

    def _btn(self, parent, text, primary, cmd):
        return self.tk.Button(parent, text=text, command=cmd, relief="flat", cursor="hand2", bd=0,
                              bg=ACCENT_HEX if primary else BG2, fg="white" if primary else FG,
                              activebackground="#e68a6b" if primary else "#2c323e", activeforeground="white",
                              font=("Segoe UI", 10))

    def open_browser(self):
        try:
            webbrowser.open(auth.build_authorize_url(self.challenge, self.state))
        except Exception as e:
            self.err.config(text="Tarayıcı açılamadı: %s" % e)

    def connect(self):
        pasted = self.var.get()
        self.btn_go.config(state="disabled")
        self.btn_open.config(state="disabled")
        self.err.config(text="Bağlanılıyor…", fg=MUTED_HEX)

        def work():
            tokens, err = auth.exchange_code(pasted, self.verifier, self.state)
            if tokens is not None and not auth.save_tokens(tokens):
                tokens, err = None, "Oturum anahtarı kaydedilemedi."
            self.q.put((tokens is not None, err))

        threading.Thread(target=work, daemon=True).start()
        self.top.after(150, self._wait)

    def _wait(self):
        try:
            ok, err = self.q.get_nowait()
        except queue.Empty:
            self.top.after(150, self._wait)
            return
        if ok:
            self.close()
            self.app.after_sign_in()
        else:
            self.err.config(text=err, fg="#f87171")
            self.btn_go.config(state="normal")
            self.btn_open.config(state="normal")

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass


# ------------------------------------------------------------------ the app

class App:
    def __init__(self, demo=False, single=None):
        import tkinter as tk
        winapi.set_dpi_aware()
        self.tk = tk
        self.demo = demo
        self.cfg = Cfg() if demo else Cfg.load()
        self.poller = auth.ApiPoller()
        self.ticker = sound.Ticker()
        self.watcher = ActivityWatcher()
        self.activity_at, self.activity_pending = 0, False
        self._sig = None
        self.cur = None
        self.hist = []
        self.view = model.build_view(None, [], False, "", store.now())
        self.hits = {}
        self.stamps = (0, 0, 0)
        self.log_path, self.log_searched = None, 0
        self.last_minute = -1
        self.notified = set()
        self.tray_key = ""
        self.press = None
        self.mode = None
        self._redraw_job = None
        self.visible = False
        self.quitting = False
        self.scale = 1.0
        self.px = self.py = self.pw = self.ph = 0

        self.root = r = tk.Tk()
        r.title("Limit Lamp")
        r.overrideredirect(True)
        r.configure(bg="black")
        r.attributes("-topmost", bool(self.cfg.top))
        r.geometry("+-3000+-3000")
        r.update_idletasks()
        self.hwnd = winapi.root_hwnd(r.winfo_id())
        winapi.make_layered(self.hwnd)
        self.dpi = winapi.get_dpi(self.hwnd)

        self.single = single
        self.tray = None
        if not demo:
            self.tray = winapi.Tray(self.toggle_visible, self.show_menu_at_cursor, os.path.join(store.DIR, "tray.ico"))

        r.bind("<Motion>", self.on_motion)
        r.bind("<ButtonPress-1>", self.on_press)
        r.bind("<B1-Motion>", self.on_drag)
        r.bind("<ButtonRelease-1>", self.on_release)
        r.bind("<Double-Button-1>", self.on_double)
        r.bind("<ButtonPress-3>", self.on_right)
        r.bind("<MouseWheel>", self.on_wheel)

        self.reload(True)
        self.place_initial()
        if self.cfg.visible or demo:
            self.visible = True
        else:
            r.withdraw()
        self.redraw()
        self.poll_now(True)
        r.after(2000, self.tick)
        r.after(700, self.check_show_signal)

    # ---------------------------------------------------------------- geometry
    def target_size(self):
        sc = self.cfg.zoom / 100.0 * self.dpi / 96.0
        return sc, int(round(WT * sc)), int(round(render.total_height(self.view, self.cfg.compact) * sc))

    def place_initial(self):
        sc, w, h = self.target_size()
        self.scale, self.pw, self.ph = sc, w, h
        x, y = self.cfg.x, self.cfg.y
        if x is None or y is None or not winapi.on_some_monitor(x + 20, y + 20):
            ax, ay, bx, by = winapi.work_area()
            x, y = bx - w - 12, by - h - 12
        self.px, self.py = int(x), int(y)

    def apply_layout(self, anchor_bottom=True):
        sc, w, h = self.target_size()
        bottom = self.py + self.ph
        self.scale, self.pw, self.ph = sc, w, h
        if anchor_bottom:
            self.py = bottom - h
        self.redraw()

    # ---------------------------------------------------------------- rendering
    def redraw(self, fast=False, force=False):
        if self._redraw_job:
            try:
                self.root.after_cancel(self._redraw_job)
            except Exception:
                pass
            self._redraw_job = None
        if not self.visible:
            return
        sig = (self.view.signature(), self.cfg.opacity, self.cfg.compact, round(self.scale, 4))
        if not fast and not force and sig == self._sig:        # nothing visible changed: skip the ~0.3 s repaint
            return
        img, hits = render.render(self.view, self.cfg.opacity, self.cfg.compact, self.scale,
                                  max_big_px=1_200_000 if fast else 5_000_000, max_ss=1 if fast else 2)
        self._sig = None if fast else sig
        self.hits = hits
        self.pw, self.ph = img.size
        self.root.geometry("%dx%d+%d+%d" % (self.pw, self.ph, self.px, self.py))
        self.root.update_idletasks()
        winapi.present(self.hwnd, img, self.px, self.py)

    def schedule_redraw(self, delay=30, fast=False):
        if self._redraw_job:
            return
        self._redraw_job = self.root.after(delay, lambda: (setattr(self, "_redraw_job", None), self.redraw(fast)))

    # ---------------------------------------------------------------- data
    def reload(self, force):
        if self.demo:
            if self.cur is None:
                self.cur = model.demo_snapshot()
                self.hist = model.demo_history()
                self.rebuild_view()
            return
        feed_t = _mtime(store.LIMITS)
        hist_t = _mtime(store.HISTORY)
        if self.log_path is None and time.time() - self.log_searched > 300:
            self.log_searched = time.time()
            self.log_path = find_desktop_log()
        elif self.log_path and not os.path.exists(self.log_path):
            self.log_path = None
        log_t = _mtime(self.log_path) if self.log_path else 0
        stamps = (feed_t, hist_t, log_t)
        if not force and stamps == self.stamps:
            return
        hist_changed = force or hist_t != self.stamps[1]
        self.stamps = stamps

        best = merge(store.read_limits(), read_desktop_log(self.log_path) if self.log_path else None)
        now = store.now()
        self.cur = choose(self.poller.last, best, now)
        n = self.ticker.feed(self.cur, now)             # weekly +1 % -> soft tick (sound only)
        if n and self.cfg.sound and not self.demo:
            sound.play(n, self.cfg.volume)
        if hist_changed:
            self.hist = store.read_history(now - 7 * 86400)
        self.rebuild_view()
        self.check_notify()
        self.after_data_change()

    def rebuild_view(self):
        self.view = model.build_view(self.cur, self.hist, self.demo or self.poller.signed_in(), self.poller.last_error, store.now())
        self.update_tray()

    def after_data_change(self):
        _, w, h = self.target_size()
        if h != self.ph or w != self.pw:
            self.apply_layout(True)
        else:
            self.redraw()
        winapi.trim_memory()

    def poll_now(self, force=False, activity=False):
        """-> True if a poll was started. force = user asked; activity = Claude Code just responded."""
        if self.demo or not self.poller.signed_in():
            return False
        now = store.now()
        if now < self.poller.next_allowed:              # server asked us to slow down
            return False
        need = 15 if (force or activity) else self.cfg.interval
        if now - self.poller.last_poll_at < need:
            return False
        if self.poller.poll_async():
            self.root.after(400, self._poll_wait)
            return True
        return False

    def _poll_wait(self):
        if self.poller.running:
            self.root.after(400, self._poll_wait)
        else:
            self.reload(True)

    def after_sign_in(self):
        self.poller.next_allowed = 0
        self.poller.last_poll_at = 0
        self.poll_now(True)
        self.reload(True)

    def tick(self):
        if self.quitting:
            return
        try:
            self.reload(False)                          # cheap: only stat()s the data files
            now = store.now()
            minute = now // 60
            if minute != self.last_minute:
                self.last_minute = minute
                self.rebuild_view()
                self.redraw()
                dpi = winapi.get_dpi(self.hwnd)
                if dpi != self.dpi:
                    self.dpi = dpi
                    self.apply_layout(False)
            if self.watcher.changed():                  # Claude Code just did something: usage probably moved
                self.activity_at, self.activity_pending = now, True
            if self.activity_pending and (now - self.activity_at >= 5 or now - self.poller.last_poll_at >= 20):
                if self.poll_now(activity=True):
                    self.activity_pending = False
            else:
                self.poll_now()
        finally:
            self.root.after(2000, self.tick)

    def check_show_signal(self):
        if self.quitting:
            return
        if self.single and self.single.poll_signal():
            self.show()
        self.root.after(700, self.check_show_signal)

    # ---------------------------------------------------------------- notifications / tray
    def check_notify(self):
        if self.demo or self.cur is None or self.tray is None:
            return
        for name, row, reset in (("5 saatlik oturum", self.view.rows[0], self.cur.five_reset),
                                 ("Haftalık limit", self.view.rows[1], self.cur.seven_reset)):
            key = "%s%d" % (name, reset)
            if row.has and row.pct >= 90 and key not in self.notified:
                self.notified.add(key)
                self.tray.balloon("Claude limiti: " + name, "%%%d kullanıldı. %s." % (round(row.pct), row.reset_text))

    def update_tray(self):
        if self.tray is None or not self.tray.ok:
            return
        r0, r1 = self.view.rows
        txt = str(int(round(r0.pct))) if r0.has else "–"
        worst = max(r0.pct if r0.has else 0, r1.pct if r1.has else 0)
        col = render.sev_color(worst) if r0.has else (85, 91, 104)
        key = "%s|%s" % (txt, col)
        tip = ("5 saat %%%d · Hafta %%%d" % (round(r0.pct), round(r1.pct))) if r0.has else "Limit Lamp: veri yok"
        if key != self.tray_key:
            self.tray_key = key
            self.tray.set(self.tray_icon(txt, col), tip)

    @staticmethod
    def tray_icon(text, color):
        from PIL import Image, ImageDraw
        s = 4
        im = Image.new("RGBA", (32 * s, 32 * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([1 * s, 1 * s, 31 * s, 31 * s], radius=9 * s, fill=color + (255,))
        f = render.font("semi", (14 if len(text) > 2 else 19) * s)
        d.text((16 * s, 17 * s), text, font=f, fill=(255, 255, 255, 255), anchor="mm")
        return im.resize((32, 32), Image.LANCZOS)

    # ---------------------------------------------------------------- visibility
    def show(self):
        self.visible = True
        self.cfg.visible = True
        self.root.deiconify()
        self.root.attributes("-topmost", bool(self.cfg.top))
        winapi.make_layered(self.hwnd)
        self.redraw(force=True)

    def hide(self):
        self.visible = False
        self.cfg.visible = False
        self.root.withdraw()
        self.cfg.save()

    def toggle_visible(self):
        self.hide() if self.visible else self.show()

    def quit(self):
        self.quitting = True
        self.cfg.save()
        if self.tray:
            self.tray.remove()
        self.root.destroy()

    # ---------------------------------------------------------------- mouse
    def to_panel(self, ex, ey):
        return ex / self.scale - M, ey / self.scale - M

    @staticmethod
    def inside(r, x, y):
        return r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]

    def hot(self, x, y):
        for k in ("refresh", "menu", "close"):
            if k in self.hits and self.inside(self.hits[k], x, y):
                return k
        if not self.view.has_data and "cta" in self.hits and self.inside(self.hits["cta"], x, y):
            return "cta"
        return None

    def edge_zone(self, x, y):
        h = render.panel_height(self.view, self.cfg.compact)
        inner, outer = 6, M
        l, r = -outer < x < inner, render.PW - inner < x < render.PW + outer
        t, b = -outer < y < inner, h - inner < y < h + outer
        if t and l: return "tl"
        if t and r: return "tr"
        if b and l: return "bl"
        if b and r: return "br"
        if l: return "l"
        if r: return "r"
        if t: return "t"
        if b: return "b"
        return None

    def on_motion(self, e):
        x, y = self.to_panel(e.x, e.y)
        z = None if self.hot(x, y) else self.edge_zone(x, y)
        self.root.config(cursor="hand2" if self.hot(x, y) else (CURSORS[z] if z else "arrow"))

    def on_press(self, e):
        x, y = self.to_panel(e.x, e.y)
        k = self.hot(x, y)
        self.mode = None
        if k == "refresh":
            self.reload(True); self.poll_now(True); return
        if k == "menu":
            self.show_menu(e.x_root, e.y_root); return
        if k == "close":
            self.hide(); return
        if k == "cta":
            self.sign_in_or_out(); return
        z = self.edge_zone(x, y)
        self.press = dict(rx=e.x_root, ry=e.y_root, x=self.px, y=self.py, w=self.pw, h=self.ph)
        self.mode = ("resize", z) if z else ("move", None)

    def on_drag(self, e):
        if not self.press or not self.mode:
            return
        p = self.press
        dx, dy = e.x_root - p["rx"], e.y_root - p["ry"]
        if self.mode[0] == "move":
            self.px, self.py = p["x"] + dx, p["y"] + dy
            self.root.geometry("+%d+%d" % (self.px, self.py))
            return
        z = self.mode[1]
        ratio = p["h"] / float(p["w"])
        if "l" in z: w = p["w"] - dx
        elif "r" in z: w = p["w"] + dx
        elif "t" in z: w = (p["h"] - dy) / ratio
        else: w = (p["h"] + dy) / ratio
        dpi = self.dpi / 96.0
        w = max(WT * 0.55 * dpi, min(WT * 3.0 * dpi, w))
        h = w * ratio
        self.px = int(p["x"] + (p["w"] - w)) if "l" in z else p["x"]
        self.py = int(p["y"] + (p["h"] - h)) if "t" in z else p["y"]
        self.scale = w / WT
        self.schedule_redraw(25, fast=True)

    def on_release(self, e):
        if self.press and self.mode and self.mode[0] == "resize":
            self.cfg.zoom = int(max(50, min(300, round(self.scale / (self.dpi / 96.0) * 100))))
            self.redraw()
        if self.press:
            self.cfg.x, self.cfg.y = self.px, self.py
            self.cfg.save()
        self.press = self.mode = None

    def on_double(self, e):
        x, y = self.to_panel(e.x, e.y)
        if y < 40 and not self.hot(x, y):
            self.set_compact(not self.cfg.compact)

    def on_right(self, e):
        self.show_menu(e.x_root, e.y_root)

    def on_wheel(self, e):
        if not (e.state & 0x4):                       # Ctrl held
            return
        f = 1.08 ** (e.delta / 120.0)
        self.cfg.zoom = int(max(50, min(300, round(self.cfg.zoom * f))))
        self.cfg.save()
        self.apply_layout(False)

    # ---------------------------------------------------------------- actions / menu
    def set_compact(self, on):
        self.cfg.compact = on
        self.cfg.save()
        self.apply_layout(True)

    def set_zoom(self, z):
        self.cfg.zoom = z
        self.cfg.save()
        self.apply_layout(True)

    def set_opacity(self, o):
        self.cfg.opacity = o
        self.cfg.save()
        self.redraw()

    def test_sound(self):
        from tkinter import messagebox
        if not sound.play(1, self.cfg.volume):
            messagebox.showwarning("Limit Lamp", "Ses çalınamadı. Windows'ta bir çıkış cihazı seçili ve ses açık mı?")

    def set_volume(self, vol):
        self.cfg.volume = vol
        self.cfg.save()
        sound.play(1, vol)                              # preview the new level right away

    def toggle_sound(self):
        self.cfg.sound = not self.cfg.sound
        self.cfg.save()

    def set_interval(self, sec):
        self.cfg.interval = sec
        self.cfg.save()

    def set_top(self):
        self.cfg.top = not self.cfg.top
        self.root.attributes("-topmost", bool(self.cfg.top))
        self.cfg.save()

    def sign_in_or_out(self):
        from tkinter import messagebox
        if self.poller.signed_in():
            if messagebox.askyesno("Limit Lamp", "Bu bilgisayardaki oturum anahtarı silinsin ve hesap bağlantısı kaldırılsın mı?\n"
                                   "(Hesabındaki yetkiyi claude.ai ayarlarından da iptal edebilirsin.)"):
                self.poller.sign_out()
                self.reload(True)
            return
        SignInDialog(self)

    def show_status(self):
        from tkinter import messagebox
        sp = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
        try:
            with open(sp, "r", encoding="utf-8") as f:
                wired = "limitlamp_feed" in f.read()
        except OSError:
            wired = False
        feed = store.read_limits()
        pl = self.poller
        msg = ("Claude hesabı girişi: %s%s\n" % ("açık" if pl.signed_in() else "kapalı",
               ("  (son durum: %s, HTTP %d)" % (pl.last_error or ("tamam" if pl.last else "bekleniyor"), pl.last_status)) if pl.signed_in() else "")
               + "Claude Code bağlantısı (statusLine): %s\n" % ("kurulu" if wired else "kurulu değil")
               + "Son Claude Code beslemesi: %s\n" % (model.ago(store.now() - feed.updated) if feed else "hiç gelmedi")
               + "Geçerli kaynak: %s\n" % (self.cur.source if self.cur else "yok")
               + "Claude Desktop günlüğü: %s\n" % (self.log_path or "bulunamadı")
               + "Veri klasörü: %s\n\n" % store.DIR
               + "Ağ trafiği yalnızca giriş yaptıysan olur (claude.ai / api.anthropic.com). Başka hiçbir yere bağlanmaz.")
        messagebox.showinfo("Limit Lamp · Bağlantı durumu", msg)

    def open_data_dir(self):
        try:
            os.makedirs(store.DIR, exist_ok=True)
            os.startfile(store.DIR)
        except OSError:
            pass

    def show_menu_at_cursor(self):
        x, y = winapi.cursor_pos()
        self.show_menu(x, y, from_tray=True)

    def make_menu(self):
        tk = self.tk
        m = tk.Menu(self.root, tearoff=0, bg="#1f232b", fg=FG, activebackground="#2c323e", activeforeground=FG,
                    selectcolor=ACCENT_HEX, bd=0, relief="flat", font=("Segoe UI", 10))
        m.add_command(label="Yenile", command=lambda: (self.reload(True), self.poll_now(True)))
        m.add_command(label="Claude hesabından çık" if self.poller.signed_in() else "Claude hesabına giriş yap…", command=self.sign_in_or_out)
        m.add_separator()
        m.add_checkbutton(label="Her zaman üstte", command=self.set_top, variable=tk.BooleanVar(value=bool(self.cfg.top)))
        m.add_checkbutton(label="Kompakt görünüm", command=lambda: self.set_compact(not self.cfg.compact), variable=tk.BooleanVar(value=self.cfg.compact))
        sub = tk.Menu(m, tearoff=0, bg="#1f232b", fg=FG, activebackground="#2c323e", activeforeground=FG, selectcolor=ACCENT_HEX, bd=0, font=("Segoe UI", 10))
        for o in OPACITIES:
            sub.add_checkbutton(label="Opak" if o == 100 else "%%%d opak" % o, command=lambda o=o: self.set_opacity(o),
                                variable=tk.BooleanVar(value=self.cfg.opacity == o))
        m.add_cascade(label="Arka plan şeffaflığı", menu=sub)
        sub2 = tk.Menu(m, tearoff=0, bg="#1f232b", fg=FG, activebackground="#2c323e", activeforeground=FG, selectcolor=ACCENT_HEX, bd=0, font=("Segoe UI", 10))
        for z in ZOOMS:
            sub2.add_checkbutton(label="%%%d" % z, command=lambda z=z: self.set_zoom(z), variable=tk.BooleanVar(value=self.cfg.zoom == z))
        m.add_cascade(label="Boyut", menu=sub2)
        m.add_checkbutton(label="Haftalık %1 artışında ses", command=self.toggle_sound, variable=tk.BooleanVar(value=self.cfg.sound))
        m.add_command(label="Sesi dene", command=self.test_sound)
        sub4 = tk.Menu(m, tearoff=0, bg="#1f232b", fg=FG, activebackground="#2c323e", activeforeground=FG, selectcolor=ACCENT_HEX, bd=0, font=("Segoe UI", 10))
        for vol, lab in VOLUMES:
            sub4.add_checkbutton(label=lab, command=lambda vol=vol: self.set_volume(vol), variable=tk.BooleanVar(value=self.cfg.volume == vol))
        m.add_cascade(label="Ses seviyesi", menu=sub4)
        sub3 = tk.Menu(m, tearoff=0, bg="#1f232b", fg=FG, activebackground="#2c323e", activeforeground=FG, selectcolor=ACCENT_HEX, bd=0, font=("Segoe UI", 10))
        for sec, lab in INTERVALS:
            sub3.add_checkbutton(label=lab, command=lambda sec=sec: self.set_interval(sec), variable=tk.BooleanVar(value=self.cfg.interval == sec))
        m.add_cascade(label="Hesap sorgu sıklığı", menu=sub3)
        m.add_checkbutton(label="Windows ile başlat", command=lambda: winapi.set_autostart(not winapi.autostart_on()),
                          variable=tk.BooleanVar(value=winapi.autostart_on()))
        m.add_separator()
        m.add_command(label="Bağlantı durumu…", command=self.show_status)
        m.add_command(label="Veri klasörünü aç", command=self.open_data_dir)
        m.add_separator()
        m.add_command(label="Gizle", command=self.hide)
        m.add_command(label="Çıkış", command=self.quit)
        return m

    def show_menu(self, x, y, from_tray=False):
        m = self.make_menu()
        try:
            winapi.foreground(self.tray.hwnd if (from_tray and self.tray and self.tray.ok) else self.hwnd)
            m.tk_popup(int(x), int(y))
        finally:
            m.grab_release()


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0


# ------------------------------------------------------------------ entry points

def run(demo=False, hold=None):
    single = None
    if not demo:
        single = winapi.SingleInstance()
        if single.already:                      # already running: ask it to show itself and leave
            single.signal_existing()
            return 0
    app = App(demo=demo, single=single)
    if hold:
        app.root.after(int(hold * 1000), app.quit)
    app.root.mainloop()
    return 0


def snapshot(path, demo, compact, scale, opacity, wallpaper, nodata=False):
    from PIL import Image
    if demo:
        view = model.build_view(model.demo_snapshot(), model.demo_history(), True, "", store.now())
    else:
        cur = merge(store.read_limits(), None)
        view = model.build_view(cur, store.read_history(store.now() - 7 * 86400), auth.token_exists(), "", store.now())
    img, _ = render.render(view, opacity, compact, scale)
    if wallpaper:
        pad = int(36 * scale)
        bg = render.paint_wallpaper(img.width + 2 * pad, img.height + 2 * pad, scale)
        bg.alpha_composite(img, (pad, pad))
        img = bg
    img.save(path)
    return 0


def make_icon(path):
    """App icon: dark rounded tile with a glowing amber lamp (used by the Start Menu shortcut)."""
    from PIL import Image, ImageDraw, ImageFilter
    S = 512
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile = render.gradient(S, S, (44, 50, 66, 255), (17, 19, 27, 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([16, 16, S - 16, S - 16], radius=112, fill=255)
    im.paste(tile, (0, 0), mask)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([S * 0.22, S * 0.22, S * 0.78, S * 0.78], fill=render.AMBER + (200,))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(46)))
    d = ImageDraw.Draw(im)
    d.ellipse([S * 0.34, S * 0.34, S * 0.66, S * 0.66], fill=render.AMBER + (255,))
    d.ellipse([S * 0.41, S * 0.39, S * 0.52, S * 0.47], fill=(255, 255, 255, 120))
    im.save(path, format="ICO", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])


def main(argv):
    args = list(argv)

    def opt(name, default=None, cast=str):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return cast(args[i + 1])
        return default

    if "--make-icon" in args:
        make_icon(opt("--make-icon"))
        return 0
    if "--snapshot" in args:
        return snapshot(opt("--snapshot"), "--demo" in args, "--compact" in args, opt("--scale", 2.0, float),
                        opt("--opacity", 88, int), "--wallpaper" in args)
    return run(demo="--demo" in args, hold=opt("--hold", None, float))
