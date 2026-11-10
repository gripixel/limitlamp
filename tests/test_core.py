"""Core tests: feeder, usage parsing, DPAPI token store, OAuth helpers.
Run:  python tests/test_core.py        (network tests only send dummy values, never real credentials)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
TMP = tempfile.mkdtemp(prefix="ll-test-")
os.environ["LIMITLAMP_DIR"] = TMP
sys.path.insert(0, APP)

from limitlamp import auth, store  # noqa: E402

FEED = os.path.join(APP, "limitlamp_feed.py")


def run_feed(payload, raw=None):
    data = raw if raw is not None else json.dumps(payload)
    p = subprocess.run([sys.executable, "-I", "-S", FEED], input=data.encode(), capture_output=True,
                       env=dict(os.environ, LIMITLAMP_DIR=TMP), timeout=30)
    return p.returncode, p.stdout.decode()


def rl(five=None, seven=None):
    d = {}
    if five:
        d["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
    if seven:
        d["seven_day"] = {"used_percentage": seven[0], "resets_at": seven[1]}
    return {"cwd": "C:/secret/project", "session_id": "abc", "model": {"display_name": "Test Model"}, "rate_limits": d}


class FeederTests(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(TMP):
            os.remove(os.path.join(TMP, f))

    def test_writes_limits_only(self):
        n = int(time.time())
        rc, out = run_feed(rl((42.5, n + 9000), (73, n + 57000)))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "5h 42% | 7d 73%")   # 42.5 rounds to even like Python's round()
        s = store.read_limits()
        self.assertAlmostEqual(s.five_pct, 42.5)
        self.assertEqual(s.seven_reset, n + 57000)
        txt = open(store.LIMITS, encoding="utf-8").read() + open(store.HISTORY).read()
        self.assertNotIn("secret", txt)
        self.assertNotIn("abc", txt)

    def test_idempotent_history(self):
        n = int(time.time())
        p = rl((10, n + 100), (20, n + 200))
        run_feed(p)
        run_feed(p)
        self.assertEqual(len(open(store.HISTORY).read().splitlines()), 1)
        p["rate_limits"]["five_hour"]["used_percentage"] = 11
        run_feed(p)
        self.assertEqual(len(open(store.HISTORY).read().splitlines()), 2)

    def test_missing_window_and_bad_input(self):
        n = int(time.time())
        rc, out = run_feed(rl(None, (74, n + 99)))
        self.assertEqual(out, "7d 74%")
        s = store.read_limits()
        self.assertFalse(s.has_five)
        before = open(store.LIMITS).read()
        self.assertEqual(run_feed({"model": {}})[0], 0)            # no rate_limits: silent, untouched
        self.assertEqual(open(store.LIMITS).read(), before)
        self.assertEqual(run_feed(None, raw="not json{")[0], 0)
        self.assertEqual(run_feed(None, raw="")[0], 0)


class ParseTests(unittest.TestCase):
    def test_legacy_shape(self):
        s = auth.parse_usage({
            "five_hour": {"utilization": 6.0, "resets_at": "2026-11-11T00:40:00.123456+00:00"},
            "seven_day": {"utilization": 73.0, "resets_at": "2026-11-11T12:00:00Z"},
            "seven_day_sonnet": {"utilization": 41, "resets_at": "2026-11-11T12:00:00Z"},
            "seven_day_opus": None,
            "seven_day_oauth_apps": {"utilization": 3, "resets_at": None},
            "extra_usage": {"is_enabled": True, "monthly_limit": 5000, "used_credits": 120, "utilization": 2.4}})
        self.assertTrue(s.has_five and s.has_seven)
        self.assertEqual(s.seven_pct, 73.0)
        self.assertEqual(s.seven_reset, 1794398400)
        self.assertEqual([m.name for m in s.models], ["Sonnet"])     # oauth_apps skipped, null skipped
        self.assertTrue(s.has_extra)

    def test_null_five_hour(self):
        s = auth.parse_usage({"five_hour": None, "seven_day": {"utilization": 10, "resets_at": None}})
        self.assertFalse(s.has_five)
        self.assertTrue(s.has_seven)
        self.assertEqual(s.seven_reset, 0)

    def test_limits_list_shape(self):
        s = auth.parse_usage({"limits": [
            {"kind": "five_hour", "group": "session", "percent": 5, "resets_at": "2026-11-11T00:40:00Z"},
            {"kind": "seven_day", "group": "weekly_all", "percent": 60, "resets_at": "2026-11-11T12:00:00Z"},
            {"kind": "seven_day_x", "group": "weekly_model", "percent": 30, "scope": {"model": "fable"}}]})
        self.assertEqual((s.five_pct, s.seven_pct), (5.0, 60.0))
        self.assertEqual(s.models[0].name, "Fable")

    def test_unrecognised(self):
        self.assertIsNone(auth.parse_usage({"hello": 1}))
        self.assertIsNone(auth.parse_usage([]))


class AuthTests(unittest.TestCase):
    def setUp(self):
        auth.clear_tokens()

    def test_dpapi_roundtrip_and_cleartext_absent(self):
        t = auth.Tokens("ACCESS-SECRET-123", "REFRESH-SECRET-456", 1234567890)
        self.assertTrue(auth.save_tokens(t))
        raw = open(os.path.join(TMP, "token.bin"), "rb").read()
        self.assertNotIn(b"ACCESS-SECRET", raw)
        self.assertNotIn(b"REFRESH-SECRET", raw)
        got = auth.load_tokens()
        self.assertEqual((got.access, got.refresh, got.expires_at), ("ACCESS-SECRET-123", "REFRESH-SECRET-456", 1234567890))
        auth.clear_tokens()
        self.assertIsNone(auth.load_tokens())

    def test_pkce_and_url(self):
        v, c, st = auth.new_pkce()
        import base64, hashlib
        self.assertEqual(c, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode())
        u = auth.build_authorize_url(c, st)
        self.assertIn("scope=user%3Aprofile", u)
        self.assertIn("code_challenge_method=S256", u)
        self.assertNotIn("user:inference", u)

    def test_state_mismatch_no_network(self):
        t, err = auth.exchange_code("somecode#WRONGSTATE", "v", "expected")
        self.assertIsNone(t)
        self.assertIn("state", err)
        self.assertEqual(auth.exchange_code("", "v", "s")[0], None)

    def test_server_rejects_dummy_code(self):
        t, err = auth.exchange_code("dummycode#st", "dummyverifier", "st")      # real server, fake code
        self.assertIsNone(t)
        self.assertIn("HTTP 400", err)

    def test_refresh_dead_with_dummy_token(self):
        n, err, dead = auth.refresh(auth.Tokens("a", "dummy-refresh", 0))
        self.assertIsNone(n)
        self.assertTrue(dead)

    def test_usage_without_token_is_401(self):
        r = auth.http("GET", auth.USAGE_URL, bearer="dummy")
        self.assertIn(r.status, (401, 403, 429))     # 429 = the server is rate-limiting this machine (it did answer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
