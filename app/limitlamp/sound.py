"""A soft "tick" when the weekly limit goes up by 1 %. Sound only - no notification.

The tick is synthesised in code (no audio files, no packages): a short, slightly deep click made of a
pitch-dropping body, a low sub tone for thickness and a tiny noise transient, kept quiet on purpose.
"""
import math
import os
import random
import struct
import wave

from . import store

RATE = 44100
PEAK = 0.30          # keep it light: ~30 % of full scale
GAP = 0.115          # seconds between ticks when the limit jumped by more than 1 %
MAX_TICKS = 3        # a big jump never turns into a machine gun


def synth_tick():
    """One tick as a list of floats in [-1, 1]."""
    n = int(RATE * 0.085)
    rng = random.Random(7)                       # deterministic click texture
    phase = 0.0
    out = []
    for i in range(n):
        t = i / RATE
        f = 400 + 300 * math.exp(-t / 0.009)     # 700 Hz dropping to 400 Hz: the "thock"
        phase += 2 * math.pi * f / RATE
        body = math.sin(phase) * math.exp(-t / 0.017)
        sub = math.sin(2 * math.pi * 200 * t) * math.exp(-t / 0.024) * 0.55     # thickness
        click = (rng.random() * 2 - 1) * math.exp(-t / 0.0011) * 0.45           # tiny transient
        out.append((body * 0.75 + sub + click) * min(1.0, t / 0.0005))          # 0.5 ms fade-in: no pop
    peak = max(abs(v) for v in out) or 1.0
    return [v / peak * PEAK for v in out]


def build_samples(ticks):
    tick = synth_tick()
    step = int(RATE * GAP)
    total = step * (ticks - 1) + len(tick) + int(RATE * 0.02)
    buf = [0.0] * total
    for k in range(ticks):
        base = k * step
        for i, v in enumerate(tick):
            buf[base + i] += v
    return buf


def write_wav(path, ticks=1):
    samples = build_samples(max(1, min(MAX_TICKS, ticks)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767)) for v in samples))
    return path


def wav_path(ticks):
    return os.path.join(store.DIR, "tick-%d.wav" % ticks)


def play(ticks=1):
    """Plays asynchronously. Never raises: no audio device / blocked file simply means no sound."""
    ticks = max(1, min(MAX_TICKS, int(ticks)))
    try:
        import winsound
        path = wav_path(ticks)
        if not os.path.exists(path):
            os.makedirs(store.DIR, exist_ok=True)
            write_wav(path, ticks)
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception:
        return False


class Ticker:
    """Decides when the weekly percentage really went up (and by how many points)."""

    MAX_GAP = 30 * 60            # newer data must follow the previous one within 30 min to count as a live increase

    def __init__(self):
        self.reset = None
        self.high = None         # highest whole-percent seen in the current weekly window
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
