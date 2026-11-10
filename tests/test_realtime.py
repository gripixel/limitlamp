"""Real-time behaviour + sound: newest data wins, poll timing, Claude Code activity trigger, weekly +1 % tick.
Run:  python tests/test_realtime.py   (no network, no window, no audio device needed)
"""
import os
import sys
import tempfile
import time
import unittest
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["LIMITLAMP_DIR"] = tempfile.mkdtemp(prefix="ll-rt-")
sys.path.insert(0, os.path.join(ROOT, "app"))

from limitlamp import app as A, sound, store  # noqa: E402
from limitlamp.store import ModelRow, Snapshot  # noqa: E402

NOW = 1_800_000_000


def snap(seven=None, five=None, updated=NOW, reset=NOW + 50000, source="Claude Code", five_reset=NOW + 9000):
    s = Snapshot()
    s.updated, s.source = updated, source
    if seven is not None:
        s.has_seven, s.seven_pct, s.seven_reset = True, seven, reset
    if five is not None:
        s.has_five, s.five_pct, s.five_reset = True, five, five_reset
    return s


class ChooseTests(unittest.TestCase):
    def test_newest_wins_over_a_recent_account_snapshot(self):
        # The bug the user hit: account says 32 (60 s ago), Claude Code just reported 34.
        api = snap(seven=32, updated=NOW - 60, source="claude.ai hesabı")
        feed = snap(seven=34, updated=NOW - 2)
        self.assertEqual(A.choose(api, feed, NOW).seven_pct, 34)

    def test_account_wins_when_it_is_newer(self):
        api = snap(seven=35, updated=NOW - 3, source="claude.ai hesabı")
        feed = snap(seven=34, updated=NOW - 90)
        self.assertEqual(A.choose(api, feed, NOW).seven_pct, 35)

    def test_account_extras_survive_a_newer_feed(self):
        api = snap(seven=32, updated=NOW - 60, source="claude.ai hesabı")
        api.models = [ModelRow("Sonnet", 20, NOW + 100)]
        feed = snap(seven=34, updated=NOW - 2)
        got = A.choose(api, feed, NOW)
        self.assertEqual((got.seven_pct, [m.name for m in got.models]), (34, ["Sonnet"]))
        self.assertEqual(feed.models, [])                      # the original is not mutated

    def test_old_account_extras_are_dropped(self):
        api = snap(seven=32, updated=NOW - 3600, source="claude.ai hesabı")
        api.models = [ModelRow("Sonnet", 20, NOW + 100)]
        self.assertEqual(A.choose(api, snap(seven=34, updated=NOW - 2), NOW).models, [])

    def test_none_cases(self):
        self.assertIsNone(A.choose(None, None, NOW))
        self.assertEqual(A.choose(None, snap(seven=1), NOW).seven_pct, 1)


class PollTimingTests(unittest.TestCase):
    class FakePoller:
        def __init__(self):
            self.next_allowed, self.last_poll_at, self.started = 0, 0, 0

        def signed_in(self):
            return True

        def poll_async(self):
            self.started += 1
            self.last_poll_at = store.now()
            return True

    class FakeRoot:
        def after(self, *a, **k):
            pass

    class FakeCfg:
        interval = 60

    def make(self):
        a = A.App.__new__(A.App)
        a.demo, a.poller, a.root, a.cfg = False, self.FakePoller(), self.FakeRoot(), self.FakeCfg()
        return a

    def test_periodic_respects_interval(self):
        a = self.make()
        a.poller.last_poll_at = store.now() - 30
        self.assertFalse(a.poll_now())                          # 30 s < 60 s
        a.poller.last_poll_at = store.now() - 61
        self.assertTrue(a.poll_now())

    def test_activity_poll_needs_only_15_seconds(self):
        a = self.make()
        a.poller.last_poll_at = store.now() - 16
        self.assertTrue(a.poll_now(activity=True))
        a.poller.last_poll_at = store.now() - 10
        self.assertFalse(a.poll_now(activity=True))             # never hammer the server

    def test_backoff_is_honoured(self):
        a = self.make()
        a.poller.next_allowed = store.now() + 300                # server said 429
        a.poller.last_poll_at = 0
        self.assertFalse(a.poll_now(force=True))
        self.assertFalse(a.poll_now(activity=True))

    def test_signed_out_or_demo_never_polls(self):
        a = self.make()
        a.demo = True
        self.assertFalse(a.poll_now(force=True))


class BackoffTests(unittest.TestCase):
    def setUp(self):
        from limitlamp import auth
        self.auth = auth
        self.orig = (auth.http, auth.load_tokens)
        auth.load_tokens = lambda: auth.Tokens("t", "r", store.now() + 3600)

    def tearDown(self):
        self.auth.http, self.auth.load_tokens = self.orig

    def reply(self, status, body="{}", retry=0.0):
        r = self.auth.HttpResult()
        r.status, r.body, r.retry_after = status, body, retry
        self.auth.http = lambda *a, **k: r

    def test_429_backs_off_gradually_and_obeys_retry_after(self):
        p = self.auth.ApiPoller()
        waits = []
        for _ in range(4):
            self.reply(429)
            t = store.now()
            p._poll()
            waits.append(p.next_allowed - t)
        self.assertTrue(waits[0] >= 90 and waits[1] > waits[0] and waits[2] > waits[1], waits)
        self.assertLessEqual(waits[3], 900 + 2)
        self.reply(429, retry=600)
        t = store.now()
        p._poll()
        self.assertGreaterEqual(p.next_allowed - t, 600)                    # the server's Retry-After wins

    def test_success_resets_the_back_off(self):
        p = self.auth.ApiPoller()
        self.reply(429)
        p._poll()
        self.assertEqual(p.rate_hits, 1)
        self.reply(200, '{"five_hour":{"utilization":5,"resets_at":null},"seven_day":{"utilization":60,"resets_at":null}}')
        p._poll()
        self.assertEqual((p.rate_hits, p.last.seven_pct, p.last_error), (0, 60.0, ""))


class ActivityTests(unittest.TestCase):
    def test_detects_new_writes_and_new_sessions(self):
        root = tempfile.mkdtemp(prefix="ll-proj-")
        proj = os.path.join(root, "proj-a")
        os.makedirs(proj)
        f1 = os.path.join(proj, "s1.jsonl")
        open(f1, "w").write("{}\n")
        w = A.ActivityWatcher(root)
        t = time.time()
        self.assertFalse(w.changed(t))                           # first look only learns the baseline
        self.assertFalse(w.changed(t + 1))
        with open(f1, "a") as f:
            f.write('{"x":1}\n')
        self.assertTrue(w.changed(t + 2))                        # a response was written
        self.assertFalse(w.changed(t + 3))                       # nothing new
        f2 = os.path.join(proj, "s2.jsonl")                      # a brand-new session appears
        open(f2, "w").write("{}\n")
        self.assertTrue(w.changed(t + 40))                       # found by the periodic full rescan

    def test_missing_folder_is_harmless(self):
        w = A.ActivityWatcher(os.path.join(tempfile.gettempdir(), "does-not-exist-ll"))
        self.assertFalse(w.changed(time.time()))


class TickerTests(unittest.TestCase):
    def test_plus_one_ticks_once(self):
        t = sound.Ticker()
        self.assertEqual(t.feed(snap(seven=32, updated=NOW), NOW), 0)               # first look: silent
        self.assertEqual(t.feed(snap(seven=33, updated=NOW + 60), NOW + 60), 1)
        self.assertEqual(t.feed(snap(seven=33, updated=NOW + 120), NOW + 120), 0)  # unchanged

    def test_bigger_jump_ticks_per_point_but_capped(self):
        t = sound.Ticker()
        t.feed(snap(seven=30), NOW)
        self.assertEqual(t.feed(snap(seven=32, updated=NOW + 30), NOW + 30), 2)
        self.assertEqual(t.feed(snap(seven=40, updated=NOW + 60), NOW + 60), sound.MAX_TICKS)

    def test_reset_time_rounding_between_sources_is_same_window(self):
        t = sound.Ticker()
        t.feed(snap(seven=32, updated=NOW, reset=NOW + 50000), NOW)
        # the other source reports the reset a second later: still the same week -> tick
        self.assertEqual(t.feed(snap(seven=33, updated=NOW + 30, reset=NOW + 50001), NOW + 30), 1)

    def test_decrease_and_reset_are_silent(self):
        t = sound.Ticker()
        t.feed(snap(seven=80), NOW)
        self.assertEqual(t.feed(snap(seven=79, updated=NOW + 30), NOW + 30), 0)
        new_window = snap(seven=2, updated=NOW + 60, reset=NOW + 50000 + 604800)
        self.assertEqual(t.feed(new_window, NOW + 60), 0)                           # new week: rebase quietly
        self.assertEqual(t.feed(snap(seven=3, updated=NOW + 90, reset=NOW + 50000 + 604800), NOW + 90), 1)

    def test_stale_source_flip_flop_does_not_false_tick(self):
        t = sound.Ticker()
        t.feed(snap(seven=34, updated=NOW), NOW)
        t.feed(snap(seven=33, updated=NOW - 600, source="Claude Desktop günlüğü", reset=0), NOW + 10)   # older, lower
        self.assertEqual(t.feed(snap(seven=34, updated=NOW + 20), NOW + 20), 0)                        # back to 34: no tick

    def test_newer_lower_value_becomes_the_new_baseline(self):
        t = sound.Ticker()
        t.feed(snap(seven=60, updated=NOW), NOW)
        self.assertEqual(t.feed(snap(seven=32, updated=NOW + 60), NOW + 60), 0)      # corrected downwards: silent
        self.assertEqual(t.feed(snap(seven=33, updated=NOW + 120), NOW + 120), 1)    # and the next +1 ticks

    def test_old_data_catching_up_is_silent(self):
        t = sound.Ticker()
        t.feed(snap(seven=60, updated=NOW - 6 * 3600, source="Claude Desktop günlüğü", reset=0), NOW)
        self.assertEqual(t.feed(snap(seven=73, updated=NOW), NOW), 0)               # 6 h gap: not a live increase

    def test_expired_or_missing_window_is_silent(self):
        t = sound.Ticker()
        self.assertEqual(t.feed(None, NOW), 0)
        self.assertEqual(t.feed(snap(five=10), NOW), 0)                              # no weekly window
        self.assertEqual(t.feed(snap(seven=50, reset=NOW - 5), NOW), 0)


class SynthTests(unittest.TestCase):
    def test_tick_is_short_quiet_and_clean(self):
        s = sound.synth_tick()
        self.assertTrue(0.05 < len(s) / sound.RATE < 0.12)
        self.assertLessEqual(max(abs(v) for v in s), sound.PEAK + 1e-9)               # light, never loud
        self.assertLess(abs(s[0]), 0.02)                                              # starts at ~0: no pop
        self.assertLess(abs(s[-1]), 0.01)                                             # ends at ~0: no click-off

    def test_tick_has_body(self):
        s = sound.synth_tick()
        head = sum(v * v for v in s[: int(sound.RATE * 0.02)])
        tail = sum(v * v for v in s[int(sound.RATE * 0.05):])
        self.assertGreater(head, tail * 5)                                            # sharp attack, quick decay

    def test_wav_files_are_valid(self):
        for n in (1, 2, 3):
            p = sound.write_wav(os.path.join(os.environ["LIMITLAMP_DIR"], "t%d.wav" % n), n)
            with wave.open(p) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, sound.RATE))
                dur = w.getnframes() / w.getframerate()
            self.assertGreater(dur, 0.08 + (n - 1) * sound.GAP * 0.9)
        self.assertLess(os.path.getsize(p), 60_000)

    def test_play_never_raises(self):
        self.assertIn(sound.play(2), (True, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
