"""View model: turns raw limit data into the texts, pace figures and graph points the panel shows."""
from datetime import datetime

from . import store
from .store import ModelRow, Snapshot

DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


class Row:
    def __init__(self, title=""):
        self.title = title
        self.has = False
        self.pct = 0.0
        self.reset = 0
        self.reset_text = ""
        self.pace_text = ""
        self.pace_color = "muted"
        self.forecast = ""
        self.ideal = -1.0                 # expected % at this point of the window (tick on the bar)
        self.spark = []                   # [(x 0..1, y 0..1)]
        self.spark_label = ""
        self.compact_time = ""


class View:
    def __init__(self):
        self.rows = [Row(), Row()]
        self.extra_rows = []
        self.footer = ""
        self.footer_color = "muted"
        self.has_data = False
        self.signed_in = False
        self.error = ""


def dur(sec):
    sec = max(0, int(sec))
    d, h, m = sec // 86400, (sec % 86400) // 3600, (sec % 3600) // 60
    if d > 0:
        return "%dg %dsa" % (d, h)
    if h > 0:
        return "%dsa %02ddk" % (h, m)
    return "%ddk" % max(m, 1)


def ago(sec):
    if sec < 90:
        return "az önce"
    if sec < 3600:
        return "%d dk önce" % (sec // 60)
    if sec < 86400:
        return "%d sa önce" % (sec // 3600)
    return "%d gün önce" % (sec // 86400)


def abs_text(epoch):
    t = datetime.fromtimestamp(epoch)
    dd = (t.date() - datetime.now().date()).days
    hm = t.strftime("%H:%M")
    if dd == 0:
        return "bugün " + hm
    if dd == 1:
        return "yarın " + hm
    return DAYS[t.weekday()] + " " + hm


def severity(pct):
    return "red" if pct >= 90 else ("amber" if pct >= 70 else "green")


def _downsample(pts, maxn):
    if len(pts) <= maxn:
        return pts
    step = len(pts) / float(maxn)
    out = [pts[min(len(pts) - 1, int(round(i * step + step - 1)))] for i in range(maxn)]
    if out[-1][0] < 1.0:
        out.append(pts[-1])
    return out


def build_row(title, have, has_win, pct, reset, window_sec, five, now, hist):
    r = Row(title)
    r.spark_label = "son 24 sa" if five else "son 7 gün"
    if not have:
        r.reset_text = "Henüz veri yok"
        return r
    r.has = True

    if reset > 0 and reset <= now:
        r.pct, r.reset_text, r.compact_time = 0.0, "Pencere yenilendi · yeni kullanımda başlar", "yenilendi"
    elif not has_win:
        r.pct, r.reset_text, r.compact_time = 0.0, "Aktif pencere yok · kullanınca başlar", "pencere yok"
    else:
        r.pct, r.reset = pct, reset
        if reset > 0:
            r.reset_text = "Sıfırlanma %s · %s kaldı" % (abs_text(reset), dur(reset - now))
            r.compact_time = dur(reset - now)
            remain = reset - now
            total = float(window_sec)
            elapsed = total - remain
            if elapsed >= total * 0.03 and remain > 0:
                ideal = elapsed / total * 100.0
                r.ideal = min(100.0, ideal)
                delta = pct - ideal
                if delta >= 5:
                    r.pace_text = "Beklenenden +%d puan hızlı" % round(delta)
                    r.pace_color = "red" if delta >= 20 else "amber"
                elif delta <= -5:
                    r.pace_text = "Beklenenden %d puan yavaş" % round(-delta)
                    r.pace_color = "green"
                else:
                    r.pace_text = "Tempo dengeli"
                if pct > 0 and elapsed >= total * 0.10:
                    rate = pct / elapsed
                    to_full = (100.0 - pct) / rate
                    if pct >= 100:
                        r.forecast = "Limit doldu"
                    elif to_full < remain:
                        r.forecast = "Bu hızla %s dolar" % abs_text(now + int(to_full))
                    else:
                        r.forecast = "Bu hızla sonda ≈%%%d" % round(min(99, pct / elapsed * total))
        else:
            r.reset_text, r.compact_time = "Sıfırlanma saati bilinmiyor", "?"

    span = 24 * 3600 if five else 7 * 86400
    start = now - span
    pts = []
    for t, f, s in hist:
        if t < start or t > now:
            continue
        v = f if five else s
        v = 0.0 if v is None else v
        pts.append((float(t - start) / span, max(0.0, min(1.0, v / 100.0))))
    if pts:
        pts.append((1.0, max(0.0, min(1.0, r.pct / 100.0))))
    r.spark = _downsample(pts, 56)
    return r


def mini_row(title, pct, reset, now):
    r = Row(title)
    r.has, r.pct, r.reset = True, pct, reset
    r.compact_time = dur(reset - now) if reset > now else ""
    return r


def build_view(cur, hist, signed_in, last_error, now):
    v = View()
    v.signed_in, v.error = signed_in, last_error or ""
    have = cur is not None
    v.has_data = have
    v.rows[0] = build_row("5 saatlik oturum", have, have and cur.has_five, cur.five_pct if have else 0,
                          cur.five_reset if have else 0, 5 * 3600, True, now, hist)
    v.rows[1] = build_row("Haftalık limit", have, have and cur.has_seven, cur.seven_pct if have else 0,
                          cur.seven_reset if have else 0, 7 * 86400, False, now, hist)
    if have:
        for m in cur.models:
            if len(v.extra_rows) < 3:
                v.extra_rows.append(mini_row(m.name + " · haftalık", m.pct, m.reset, now))
        if cur.has_extra and len(v.extra_rows) < 3:
            v.extra_rows.append(mini_row("Ek kullanım · aylık", cur.extra_pct, 0, now))

    err = v.error
    if not have:
        if signed_in:
            v.footer = err or "Veri alınıyor…"
            v.footer_color = "amber" if not err else "red"
        else:
            v.footer, v.footer_color = "Veri yok · giriş yap ya da Claude Code'u bağla", "red"
    else:
        age = now - cur.updated
        if err and cur.source == "claude.ai hesabı":
            v.footer, v.footer_color = "%s · son veri %s" % (err, ago(age)), "amber"
        elif age < 15 * 60:
            v.footer, v.footer_color = "Canlı · %s · %s" % (cur.source, ago(age)), "green"
        else:
            v.footer = "Son veri %s · %s" % (ago(age), cur.source)
            v.footer_color = "amber" if age < 6 * 3600 else "red"
    return v


# ------------------------------------------------------------------ demo data (screenshots / --demo)

def demo_snapshot():
    now = store.now()
    s = Snapshot()
    s.has_five, s.five_pct, s.five_reset = True, 42.0, now + 3 * 3600 + 24 * 60
    s.has_seven, s.seven_pct, s.seven_reset = True, 73.0, now + 16 * 3600 + 5 * 60
    s.updated, s.source = now - 40, "claude.ai hesabı"
    s.models = [ModelRow("Sonnet", 38.0, s.seven_reset), ModelRow("Opus", 11.0, s.seven_reset)]
    return s


def demo_history():
    now = store.now()
    out, sv = [], 0.0
    t = now - 6 * 86400 - 6 * 3600
    while t <= now:
        day = (now - t) / 86400.0
        inc = (0.35 if day > 5.2 else (1.15 if ((t // 1800) % 11) < 4 else 0.12)) * 1.15
        sv = min(73.0, sv + inc)
        age = (now - t) / 3600.0
        if age < 1.6:
            five = max(0.0, 42 - age * 24)
        elif age < 8:
            five = min(88.0, (8 - age) * 14 + 6)
        else:
            five = float((((t // 1800) * 7) % 52) + 4)
        out.append((t, five, sv))
        t += 1800
    return out
