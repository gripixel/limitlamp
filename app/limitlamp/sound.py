"""A soft "tick" when the weekly limit goes up by 1 %. Sound only - no notification.

The tick is synthesised in code (no audio files, no packages): a short wooden "thock" - a pitch-dropping body
with a second partial, a little low warmth and a tiny high transient so it is audible on laptop speakers too.
Most of its energy sits at 300-3000 Hz (small speakers cannot reproduce anything much below ~250 Hz).
"""
import math
import os
import random
import struct
import wave

from . import store

RATE = 44100
PEAK = 0.54          # default peak (volume 60 %); the menu offers light / medium / loud
GAP = 0.115          # seconds between ticks when the limit jumped by more than 1 %
MAX_TICKS = 3        # a big jump never turns into a machine gun
DEFAULT_VOLUME = 60


def peak_for(volume):
    """Menu volume (10..100 %) -> peak amplitude (0..0.9)."""
    return max(0.05, min(1.0, volume / 100.0)) * 0.9


def synth_tick(peak=PEAK):
    """One tick as a list of floats in [-peak, peak]."""
    n = int(RATE * 0.09)
    rng = random.Random(7)                       # deterministic click texture
    ph1 = ph2 = 0.0
    prev = 0.0
    out = []
    for i in range(n):
        t = i / RATE
        f1 = 430 + 300 * math.exp(-t / 0.008)    # 730 Hz dropping to 430 Hz: the "thock"
        ph1 += 2 * math.pi * f1 / RATE
        ph2 += 2 * math.pi * f1 * 1.52 / RATE    # second partial: woody, not a pure beep
        body = math.sin(ph1) * math.exp(-t / 0.016) + 0.45 * math.sin(ph2) * math.exp(-t / 0.010)
        warm = math.sin(2 * math.pi * 240 * t) * math.exp(-t / 0.022) * 0.40         # thickness without needing bass
        nz = rng.random() * 2 - 1
        hp = nz - prev                                                                # crude high-pass: bright transient
        prev = nz * 0.55
        click = hp * math.exp(-t / 0.0014) * 0.55
        out.append((body * 0.8 + warm + click) * min(1.0, t / 0.0005))               # 0.5 ms fade-in: no pop
    m = max(abs(v) for v in out) or 1.0
    return [v / m * peak for v in out]


def build_samples(ticks, peak=PEAK):
    tick = synth_tick(peak)
    step = int(RATE * GAP)
    total = step * (ticks - 1) + len(tick) + int(RATE * 0.02)
    buf = [0.0] * total
    for k in range(ticks):
        base = k * step
        for i, v in enumerate(tick):
            buf[base + i] += v
    return buf


def write_wav(path, ticks=1, peak=PEAK):
    samples = build_samples(max(1, min(MAX_TICKS, ticks)), peak)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767)) for v in samples))
    return path


def wav_path(ticks, volume=DEFAULT_VOLUME):
    return os.path.join(store.DIR, "tick-%d-v%d.wav" % (ticks, volume))


def play(ticks=1, volume=DEFAULT_VOLUME):
    """Plays asynchronously. Returns False (never raises) if it could not play."""
    ticks = max(1, min(MAX_TICKS, int(ticks)))
    try:
        import winsound
        path = wav_path(ticks, volume)
        if not os.path.exists(path):
            os.makedirs(store.DIR, exist_ok=True)
            write_wav(path, ticks, peak_for(volume))
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception:
        return False


class Ticker:
    """Decides when the weekly percentage really went up (and by how many points)."""

    MAX_GAP = 30 * 60            # newer data must follow the previous one within 30 min to count as a live increase

    def __init__(self):
        self.reset = None
        self.high = None         # last whole-percent seen in the current weekly window
        self.updated = 0

    def feed(self, snap, now):
        """-> number of ticks to play (0..MAX_TICKS)."""
        if snap is None or not snap.has_seven:
            return 0
        if snap.seven_reset and snap.seven_reset <= now:          # window already over
            self.reset = self.high = None
            return 0
        pct = int(round(snap.seven_pct))
        # Different sources round the reset time differently (seconds apart); windows are a week apart,
        # so anything within an hour is the same window.
        same_window = (self.high is not None
                       and (not self.reset or not snap.seven_reset or abs(self.reset - snap.seven_reset) < 3600))
        n = 0
        if same_window:
            if snap.updated < self.updated:                         # older than what we already saw: ignore it
                return 0
            if pct > self.high and snap.updated - self.updated <= self.MAX_GAP:
                n = min(MAX_TICKS, pct - self.high)
            self.high, self.updated = pct, snap.updated             # newer data is the new baseline (even if lower)
        else:                                                       # first look, or a new weekly window: rebase quietly
            self.high, self.updated = pct, snap.updated
        if snap.seven_reset:
            self.reset = snap.seven_reset
        return n
