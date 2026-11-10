"""Drawing engine (Pillow). Produces an RGBA image with real per-pixel alpha: only the panel background
is translucent, text and bars stay sharp. Everything scales with `scale` (pixels per logical unit)."""
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

M = 10            # shadow margin (logical px)
PW = 360          # panel width (logical px)
WT = PW + 2 * M   # total logical width

TEXT = (238, 240, 245)
MUTED = (154, 162, 177)
FAINT = (112, 120, 136)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
RED = (248, 113, 113)
ACCENT = (217, 119, 87)
NAMED = {"green": GREEN, "amber": AMBER, "red": RED, "muted": MUTED}


def sev_color(pct):
    return RED if pct >= 90 else (AMBER if pct >= 70 else GREEN)


def mix(c, d):
    return tuple(max(0, min(255, v + d)) for v in c)


def rgba(c, a):
    return (c[0], c[1], c[2], max(0, min(255, int(a))))


# ---------------------------------------------------------------- layout

def panel_height(view, compact):
    if not view.has_data:
        return 232.0
    if compact:
        return 132.0
    h = 44 + 136 + 8 + 136
    n = len(view.extra_rows)
    if n:
        h += 8 + n * 42 - 6
    return float(h + 34)


def total_height(view, compact):
    return panel_height(view, compact) + 2 * M


# ---------------------------------------------------------------- fonts

_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_FILES = {"reg": ["segoeui.ttf", "arial.ttf"], "semi": ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"]}
_fonts = {}


def font(kind, px):
    key = (kind, round(px, 2))
    f = _fonts.get(key)
    if f is None:
        for name in _FILES[kind]:
            try:
                f = ImageFont.truetype(os.path.join(_FONT_DIR, name), px)
                break
            except OSError:
                continue
        if f is None:
            f = ImageFont.load_default(px)
        if len(_fonts) > 40:
            _fonts.clear()
        _fonts[key] = f
    return f


def clear_font_cache():
    _fonts.clear()


# ---------------------------------------------------------------- gradients / primitives

_LIN = None
_SOFT = {}


def soft_disc(d):
    """Soft round falloff mask (255 in the middle, 0 at the rim), cached per size."""
    m = _SOFT.get(d)
    if m is None:
        m = Image.new("L", (d, d), 0)
        ImageDraw.Draw(m).ellipse([d * 0.2, d * 0.2, d * 0.8, d * 0.8], fill=255)
        m = m.filter(ImageFilter.GaussianBlur(d * 0.14))
        if len(_SOFT) > 16:
            _SOFT.clear()
        _SOFT[d] = m
    return m


def _linear():
    global _LIN
    if _LIN is None:
        _LIN = Image.linear_gradient("L")
    return _LIN


def gradient(w, h, c1, c2, horizontal=False):
    base = _linear()
    if horizontal:
        base = base.transpose(Image.Transpose.ROTATE_90)
    mask = base.resize((max(1, w), max(1, h)), Image.BILINEAR)
    return Image.composite(Image.new("RGBA", mask.size, c2), Image.new("RGBA", mask.size, c1), mask)


class Painter:
    """Draws in panel-relative logical coordinates onto a supersampled layer."""

    def __init__(self, size, k):
        self.img = Image.new("RGBA", size, (0, 0, 0, 0))
        self.k = k                              # pixels per logical unit (supersampled)
        self.texts = []

    # logical -> pixel
    def X(self, v):
        return (M + v) * self.k

    def _box(self, x0, y0, x1, y1, pad=0):
        bx0, by0 = int(math.floor(self.X(x0))) - pad, int(math.floor(self.X(y0))) - pad
        bx1, by1 = int(math.ceil(self.X(x1))) + pad, int(math.ceil(self.X(y1))) + pad
        return bx0, by0, max(1, bx1 - bx0), max(1, by1 - by0)

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=1.0):
        bx, by, w, h = self._box(x0, y0, x1, y1)
        tmp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).rounded_rectangle(
            [self.X(x0) - bx, self.X(y0) - by, self.X(x1) - bx - 1, self.X(y1) - by - 1], radius=r * self.k,
            fill=fill, outline=outline, width=max(1, int(round(width * self.k))))
        self.img.alpha_composite(tmp, (bx, by))

    def rrect_grad(self, x0, y0, x1, y1, r, c1, c2, horizontal=False, clip_h=None):
        bx, by, w, h = self._box(x0, y0, x1, y1)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [self.X(x0) - bx, self.X(y0) - by, self.X(x1) - bx - 1, self.X(y1) - by - 1], radius=r * self.k, fill=255)
        g = gradient(w, h, c1, c2, horizontal)
        g.putalpha(ImageChops.multiply(g.getchannel("A"), mask))
        self.img.alpha_composite(g, (bx, by))
        return bx, by, w, h, mask

    def glow(self, cx, cy, radius, color, alpha):
        d = max(2, int(radius * 2 * self.k))
        a = soft_disc(d).point(lambda v: v * alpha // 255)
        tmp = Image.new("RGBA", (d, d), color + (0,))
        tmp.putalpha(a)
        self.img.alpha_composite(tmp, (int(self.X(cx) - d / 2), int(self.X(cy) - d / 2)))

    def disc(self, cx, cy, r, color):
        bx, by, w, h = self._box(cx - r, cy - r, cx + r, cy + r, pad=1)
        tmp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).ellipse([self.X(cx - r) - bx, self.X(cy - r) - by, self.X(cx + r) - bx, self.X(cy + r) - by], fill=color)
        self.img.alpha_composite(tmp, (bx, by))

    def lines(self, pts, color, width, closed=False):
        xs = [self.X(p[0]) for p in pts]
        ys = [self.X(p[1]) for p in pts]
        pad = int(width * self.k) + 4
        bx, by = int(min(xs)) - pad, int(min(ys)) - pad
        w, h = int(max(xs)) - bx + pad + 1, int(max(ys)) - by + pad + 1
        tmp = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
        d = ImageDraw.Draw(tmp)
        px = [(x - bx, y - by) for x, y in zip(xs, ys)]
        d.line(px, fill=color, width=max(1, int(round(width * self.k))), joint="curve")
        r = width * self.k / 2
        for p in (px[0], px[-1]):
            d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)
        self.img.alpha_composite(tmp, (bx, by))

    def text(self, x, y, s, kind, px, color, align="l", maxw=None):
        self.texts.append((x, y, s, kind, px, color, align, maxw))


# ---------------------------------------------------------------- spline for the mini graph

def _catmull(pts, steps=7):
    if len(pts) < 3:
        return pts
    out = []
    p = [pts[0]] + pts + [pts[-1]]
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for s in range(steps):
            t = s / float(steps)
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                     + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in (0, 1)))
    out.append(pts[-1])
    return out


# ---------------------------------------------------------------- panel pieces

def _bar(P, x, y, w, h, pct, ideal, sev):
    P.rrect(x, y, x + w, y + h, h / 2, fill=(255, 255, 255, 34))
    if pct > 0:
        fw = max(h, min(100.0, pct) / 100.0 * w)
        P.rrect(x - 1, y - 2, x + fw + 1, y + h + 2, (h + 4) / 2, fill=rgba(sev, 38))                # soft glow
        bx, by, bw, bh, mask = P.rrect_grad(x, y, x + fw, y + h, h / 2, rgba(mix(sev, -45), 255), rgba(mix(sev, 22), 255), horizontal=True)
        sheen = gradient(bw, max(1, int(bh * 0.6)), (255, 255, 255, 70), (255, 255, 255, 0))
        a = ImageChops.multiply(sheen.getchannel("A"), mask.crop((0, 0, bw, sheen.height)))
        sheen.putalpha(a)
        P.img.alpha_composite(sheen, (bx, by))
    if ideal >= 0:
        ix = x + ideal / 100.0 * w
        P.lines([(ix, y - 3.5), (ix, y + h + 3.5)], (255, 255, 255, 215), 1.6)


def _spark(P, pts, x, y, w, h, color):
    P.lines([(x, y + h), (x + w, y + h)], (255, 255, 255, 34), 1.0)
    if len(pts) < 3:
        P.text(x + w, y + 12, "veri toplanıyor", "reg", 11, FAINT, "r")
        return
    arr = [(x + px * w, y + h - 1 - py * (h - 2)) for px, py in pts]
    curve = _catmull(arr)
    # fill under the curve (vertical gradient, clipped to the spark box)
    bx, by, bw, bh = P._box(x - 3, y - 4, x + w + 3, y + h + 4)
    mask = Image.new("L", (bw, bh), 0)
    poly = [(P.X(px) - bx, P.X(py) - by) for px, py in curve]
    poly += [(P.X(curve[-1][0]) - bx, P.X(y + h) - by), (P.X(curve[0][0]) - bx, P.X(y + h) - by)]
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    g = gradient(bw, bh, rgba(color, 95), rgba(color, 0))
    g.putalpha(ImageChops.multiply(g.getchannel("A"), mask))
    P.img.alpha_composite(g, (bx, by))
    P.lines(curve, rgba(color, 255), 1.7)
    ex, ey = arr[-1]
    P.disc(ex, ey, 5, rgba(color, 70))
    P.disc(ex, ey, 2.6, rgba(color, 255))


def _card(P, x0, y0, x1, y1, r):
    P.rrect(x0, y0, x1, y1, r, fill=(255, 255, 255, 20), outline=(255, 255, 255, 30), width=1.0)


def _pct_text(v):
    return str(int(round(v)))


def _draw_row(P, r, y):
    _card(P, 12, y, 348, y + 136, 14)
    sev = sev_color(r.pct) if r.has else FAINT
    P.text(26, y + 11, r.title, "reg", 12, MUTED)
    P.texts.append(("big", 25, y + 21, _pct_text(r.pct) if r.has else "—", r.has))
    _spark(P, r.spark, 206, y + 16, 128, 38, sev)
    P.text(334, y + 56, r.spark_label, "reg", 11, FAINT, "r")
    _bar(P, 26, y + 79, 308, 11, r.pct if r.has else 0, r.ideal, sev)
    P.text(26, y + 98, r.reset_text, "reg", 12, TEXT, "l", 308)
    if r.pace_text:
        P.texts.append(("pill", 26, y + 115, r.pace_text, NAMED[r.pace_color], r.forecast))


def _draw_mini(P, r, y):
    _card(P, 12, y, 348, y + 36, 11)
    sev = sev_color(r.pct)
    P.text(24, y + 4, r.title, "reg", 11, MUTED, "l", 200)
    if r.compact_time:
        P.text(24, y + 19, r.compact_time, "reg", 11, FAINT)
    _bar(P, 112, y + 15, 180, 7, r.pct, -1, sev)
    P.text(338, y + 9, "%" + _pct_text(r.pct), "semi", 12, TEXT, "r")


def _draw_compact(P, r, y, name):
    _card(P, 12, y, 348, y + 38, 12)
    sev = sev_color(r.pct) if r.has else FAINT
    P.text(24, y + 4, name, "semi", 12, TEXT)
    P.text(24, y + 20, r.compact_time, "reg", 11, FAINT)
    _bar(P, 102, y + 14, 186, 9, r.pct if r.has else 0, r.ideal, sev)
    P.text(340, y + 8, "%" + _pct_text(r.pct) if r.has else "—", "semi", 13, TEXT if r.has else FAINT, "r")


def _header(P, view, hits):
    worst = max(view.rows[0].pct if view.rows[0].has else 0, view.rows[1].pct if view.rows[1].has else 0)
    lamp = sev_color(worst) if (view.rows[0].has or view.rows[1].has) else FAINT
    P.glow(21, 20, 12, lamp, 130)
    P.disc(21, 20, 4.5, rgba(lamp, 255))
    P.disc(20.2, 18.8, 1.5, (255, 255, 255, 120))
    P.text(40, 11, "Limit Lamp", "semi", 13, TEXT)
    hits["refresh"] = (PW - 90, 7, 26, 26)
    hits["menu"] = (PW - 62, 7, 26, 26)
    hits["close"] = (PW - 34, 7, 26, 26)
    ic = rgba(MUTED, 235)
    cx, cy = PW - 90 + 13, 7 + 13
    pts = [(cx + 5 * math.cos(math.radians(a)), cy + 5 * math.sin(math.radians(a))) for a in range(-60, 221, 20)]
    P.lines(pts, ic, 1.6)
    P.lines([(cx + 2.5, cy - 5.7), (cx + 5.4, cy - 5.2), (cx + 4.7, cy - 2.2)], ic, 1.6)
    for i in (-1, 0, 1):
        P.disc(PW - 62 + 13 + i * 5.2, 7 + 13, 1.6, ic)
    P.lines([(PW - 34 + 8.5, 7 + 8.5), (PW - 34 + 17.5, 7 + 17.5)], ic, 1.6)
    P.lines([(PW - 34 + 17.5, 7 + 8.5), (PW - 34 + 8.5, 7 + 17.5)], ic, 1.6)


def _footer(P, view, ph):
    fy = ph - 30
    c = NAMED[view.footer_color]
    P.glow(23.5, fy + 12.5, 7, c, 110)
    P.disc(23.5, fy + 12.5, 3.5, rgba(c, 255))
    P.text(36, fy + 5, view.footer, "reg", 11, MUTED, "l", PW - 48)


def _no_data(P, view, ph, hits):
    _card(P, 12, 44, 348, ph - 12, 14)
    signed = view.signed_in
    P.text(180, 58, "Veri alınıyor…" if signed else "Canlı veri için giriş yap", "semi", 13, TEXT, "c")
    msg = (view.error or "Birkaç saniye içinde görünecek.") if signed else "Limit Lamp kullanımını doğrudan Claude hesabından okur."
    P.text(180, 84, msg, "reg", 11, MUTED, "c", PW - 60)
    if not signed:
        P.text(180, 102, "Tek seferlik giriş yeterli.", "reg", 11, MUTED, "c")
        hits["cta"] = (70, 140, PW - 140, 36)
        P.rrect_grad(70, 140, 70 + PW - 140, 176, 18, rgba(mix(ACCENT, 18), 255), rgba(mix(ACCENT, -22), 255))
        P.texts.append(("ctatext", 180, 150, "Claude ile giriş yap"))


# ---------------------------------------------------------------- main entry

def render(view, opacity_pct, compact, scale, max_big_px=7_000_000, max_ss=3):
    """-> (PIL RGBA image of size round(WT*scale) x round(total_h*scale), hit rects in panel logical coords)."""
    ph = panel_height(view, compact)
    th = ph + 2 * M
    W, H = max(40, int(round(WT * scale))), max(40, int(round(th * scale)))
    ss = max(1, int(max_ss))
    while ss > 1 and W * H * ss * ss > max_big_px:
        ss -= 1
    k = scale * ss
    P = Painter((W * ss, H * ss), k)
    hits = {}
    bg_a = int(round(255 * opacity_pct / 100.0))

    # panel background: translucent vertical gradient + sheen + border
    bx, by, bw, bh, mask = P.rrect_grad(0, 0, PW, ph, 16, (30, 34, 46, bg_a), (14, 16, 22, bg_a))
    sheen = gradient(bw, int(70 * k), (255, 255, 255, min(26, bg_a // 8)), (255, 255, 255, 0))
    sheen.putalpha(ImageChops.multiply(sheen.getchannel("A"), mask.crop((0, 0, bw, sheen.height))))
    P.img.alpha_composite(sheen, (bx, by))
    P.rrect(0, 0, PW, ph, 16, outline=(255, 255, 255, min(70, 30 + bg_a // 8)), width=1.0)

    _header(P, view, hits)
    if not view.has_data:
        _no_data(P, view, ph, hits)
    elif compact:
        _draw_compact(P, view.rows[0], 40, "5 saat")
        _draw_compact(P, view.rows[1], 84, "Hafta")
    else:
        _draw_row(P, view.rows[0], 44)
        _draw_row(P, view.rows[1], 188)
        y = 332
        for er in view.extra_rows:
            _draw_mini(P, er, y)
            y += 42
        _footer(P, view, ph)

    img = P.img.resize((W, H), Image.LANCZOS) if ss > 1 else P.img

    # soft drop shadow (final resolution), excluded under the translucent panel
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).rounded_rectangle(
        [(M) * scale, (M + 3) * scale, (M + PW) * scale, (M + ph + 3) * scale], radius=18 * scale, fill=110)
    sh = sh.filter(ImageFilter.GaussianBlur(5.5 * scale))
    ImageDraw.Draw(sh).rounded_rectangle([M * scale, M * scale, (M + PW) * scale - 1, (M + ph) * scale - 1], radius=16 * scale, fill=0)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow.putalpha(sh)
    out = Image.alpha_composite(shadow, img)

    _draw_texts(out, P.texts, scale)
    return out, hits


def _fit(txt, f, maxw):
    if maxw is None or f.getlength(txt) <= maxw:
        return txt
    while len(txt) > 1 and f.getlength(txt + "…") > maxw:
        txt = txt[:-1]
    return txt + "…"


def _draw_texts(out, texts, s):
    d = ImageDraw.Draw(out)
    ox = M * s
    for t in texts:
        if t[0] == "big":                                  # big number + small %
            _, x, y, num, has = t
            fb, fp = font("semi", 36 * s), font("semi", 15 * s)
            col = TEXT if has else FAINT
            d.text((ox + x * s, ox + y * s), num, font=fb, fill=col + (255,), anchor="la")
            if has:
                w = fb.getlength(num)
                d.text((ox + x * s + w + 2 * s, ox + (y + 8) * s), "%", font=fp, fill=MUTED + (255,), anchor="la")
        elif t[0] == "pill":                               # pace pill + forecast
            _, x, y, txt, col, fc = t
            f = font("reg", 11 * s)
            w = f.getlength(txt)
            x0, y0 = ox + x * s, ox + y * s
            pill = Image.new("RGBA", (int(w + 16 * s) + 2, int(17 * s) + 2), (0, 0, 0, 0))
            ImageDraw.Draw(pill).rounded_rectangle([0, 0, pill.width - 1, pill.height - 1], radius=8.5 * s, fill=rgba(col, 36))
            out.alpha_composite(pill, (int(x0), int(y0)))
            d.text((x0 + 8 * s, y0 + 1.2 * s), txt, font=f, fill=col + (255,), anchor="la")
            if fc:
                right = ox + (12 + 336 - 14) * s
                left = x0 + w + 16 * s + 8 * s
                d.text((right, y0 + 1.7 * s), _fit(fc, f, right - left), font=f, fill=MUTED + (255,), anchor="ra")
        elif t[0] == "ctatext":
            _, x, y, txt = t
            d.text((ox + x * s, ox + y * s), txt, font=font("semi", 12 * s), fill=(255, 255, 255, 255), anchor="ma")
        else:
            x, y, txt, kind, px, color, align, maxw = t
            f = font(kind, px * s)
            txt = _fit(txt, f, maxw * s if maxw else None)
            anchor = {"l": "la", "r": "ra", "c": "ma"}[align]
            d.text((ox + x * s, ox + y * s), txt, font=f, fill=color + (255,), anchor=anchor)


# ---------------------------------------------------------------- README preview helper

def paint_wallpaper(w, h, s):
    img = gradient(w, h, (34, 52, 110, 255), (150, 70, 120, 255)).convert("RGB")
    d0 = int(max(w, h) * 0.9)
    layer = Image.new("RGB", (d0, d0), (90, 160, 255))
    img.paste(layer, (int(-w * 0.15), int(-h * 0.2)), soft_disc(d0).point(lambda v: v * 120 // 255))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([w * 0.08, h * 0.20, w * 0.78, h * 0.50], radius=10 * s, fill=(255, 255, 255, 70))
    for i in range(9):
        ww = w * (0.30 + 0.28 * ((i * 37) % 7) / 7.0)
        d.rectangle([w * 0.11, h * (0.24 + i * 0.026), w * 0.11 + ww, h * (0.24 + i * 0.026) + 3 * s], fill=(255, 255, 255, 120))
    d.rounded_rectangle([w * 0.45, h * 0.60, w * 0.95, h * 0.88], radius=10 * s, fill=(255, 255, 255, 70))
    return img.convert("RGBA")
