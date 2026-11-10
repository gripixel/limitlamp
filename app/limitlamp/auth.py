"""Optional Claude account sign-in (OAuth PKCE) and usage polling.

- Sign-in happens in your browser; your password is never typed into this program.
- Only the `user:profile` scope is requested (enough to read usage).
- The token is encrypted with Windows DPAPI (bound to your Windows user) before it touches the disk.
- Talks only to platform.claude.com, claude.ai (in your browser) and api.anthropic.com. No telemetry.
"""
import base64
import ctypes
import hashlib
import json
import os
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone

from . import store
from .store import ModelRow, Snapshot

# Honest identity: this program does not pretend to be another client.
UA = "LimitLamp/2.0 (+https://github.com/gripixel/limitlamp)"

# Claude Code's public OAuth client id: this flow does not work with another id.
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
SCOPE = "user:profile"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
SKIP_SUFFIX = ("oauth_apps", "cowork", "routines")

_ENTROPY = b"LimitLamp.token.v1"


# ----------------------------------------------------------------- HTTP

class HttpResult:
    def __init__(self):
        self.status = 0          # 0 = network error
        self.body = ""
        self.error = ""
        self.retry_after = 0.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = None


def _get_opener():
    """One TLS context for the whole run (loading the Windows certificate store on every poll is the costly part)."""
    global _opener
    if _opener is None:
        import ssl
        _opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    return _opener


def http(method, url, body=None, bearer=None, timeout=20):
    res = HttpResult()
    headers = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
        headers["anthropic-beta"] = "oauth-2025-04-20"
        headers["anthropic-version"] = "2023-06-01"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _get_opener().open(req, timeout=timeout) as r:
            res.status = r.status
            res.body = r.read(400000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        res.status = e.code
        try:
            res.body = e.read(400000).decode("utf-8", "replace")
        except Exception:
            pass
        try:
            res.retry_after = float(e.headers.get("Retry-After", "") or 0)
        except ValueError:
            pass
    except Exception as e:
        res.error = "%s" % (getattr(e, "reason", None) or e)
    return res


# ----------------------------------------------------------------- DPAPI token store

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt(fn, data):
    inb, keep1 = _blob(data)
    ent, keep2 = _blob(_ENTROPY)
    out = _BLOB()
    if not fn(ctypes.byref(inb), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class Tokens:
    def __init__(self, access="", refresh="", expires_at=0):
        self.access, self.refresh, self.expires_at = access, refresh, expires_at


def _token_path():
    return os.path.join(store.DIR, "token.bin")


def token_exists():
    return os.path.exists(_token_path())


def save_tokens(t):
    try:
        os.makedirs(store.DIR, exist_ok=True)
        plain = ("%s\n%s\n%d" % (t.access, t.refresh, t.expires_at)).encode("utf-8")
        enc = _crypt(ctypes.windll.crypt32.CryptProtectData, plain)
        if enc is None:
            return False
        tmp = _token_path() + ".tmp"
        with open(tmp, "wb") as f:
            f.write(enc)
        os.replace(tmp, _token_path())
        return True
    except OSError:
        return False


def load_tokens():
    try:
        with open(_token_path(), "rb") as f:
            enc = f.read()
    except OSError:
        return None
    dec = _crypt(ctypes.windll.crypt32.CryptUnprotectData, enc)
    if dec is None:
        return None
    parts = dec.decode("utf-8", "replace").split("\n")
    if len(parts) < 3 or not parts[0]:
        return None
    try:
        return Tokens(parts[0], parts[1], int(parts[2]))
    except ValueError:
        return None


def clear_tokens():
    try:
        os.remove(_token_path())
    except OSError:
        pass


# ----------------------------------------------------------------- OAuth

def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def new_pkce():
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(24))
    return verifier, challenge, state


def build_authorize_url(challenge, state):
    q = urllib.parse.urlencode({
        "code": "true", "client_id": CLIENT_ID, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    return AUTHORIZE_URL + "?" + q


def _err_text(r):
    if r.status == 0:
        return "Ağ hatası: " + r.error
    try:
        d = json.loads(r.body)
        if isinstance(d, dict):
            for k in ("error_description", "error"):
                if d.get(k):
                    return "HTTP %d: %s" % (r.status, d[k])
    except ValueError:
        pass
    return "HTTP %d" % r.status


def _parse_tokens(body):
    try:
        d = json.loads(body)
        t = Tokens(str(d["access_token"]), str(d.get("refresh_token") or ""),
                   store.now() + int(float(d.get("expires_in") or 3600)))
        return t, ""
    except (ValueError, KeyError, TypeError):
        return None, "Beklenmeyen yanıt"


def exchange_code(pasted, verifier, expected_state):
    """-> (Tokens|None, error text). `pasted` has the form code#state."""
    pasted = (pasted or "").strip()
    if not pasted:
        return None, "Kodu yapıştır."
    code, st = pasted, expected_state
    if "#" in pasted:
        code, st = [x.strip() for x in pasted.split("#", 1)]
    if st != expected_state:
        return None, "Kod bu oturuma ait değil (state uyuşmuyor). “Tarayıcıda aç”a tekrar tıkla."
    r = http("POST", TOKEN_URL, {
        "grant_type": "authorization_code", "code": code, "state": st, "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI, "code_verifier": verifier})
    if r.status != 200:
        return None, _err_text(r)
    return _parse_tokens(r.body)


def refresh(old):
    """-> (Tokens|None, error text, dead). dead = the refresh token is finished: sign in again."""
    r = http("POST", TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": old.refresh, "client_id": CLIENT_ID})
    if r.status != 200:
        return None, _err_text(r), r.status in (400, 401)
    t, err = _parse_tokens(r.body)
    if t is not None and not t.refresh:
        t.refresh = old.refresh
    return t, err, False


# ----------------------------------------------------------------- usage parsing

def _iso_to_epoch(v):
    if not isinstance(v, str) or not v:
        return 0
    s = v.strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pretty(raw):
    s = raw.replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else raw


def parse_usage(root):
    """Supports both known response shapes. Returns a Snapshot, or None if nothing recognisable."""
    if not isinstance(root, dict):
        return None
    s = Snapshot()
    s.source = "claude.ai hesabı"
    s.updated = store.now()

    for k, d in root.items():
        if not isinstance(d, dict) or not _num(d.get("utilization")):
            continue
        u, reset = float(d["utilization"]), _iso_to_epoch(d.get("resets_at"))
        if k == "five_hour":
            s.has_five, s.five_pct, s.five_reset = True, u, reset
        elif k == "seven_day":
            s.has_seven, s.seven_pct, s.seven_reset = True, u, reset
        elif k.startswith("seven_day_"):
            n = k[len("seven_day_"):]
            if n not in SKIP_SUFFIX:
                s.models.append(ModelRow(_pretty(n), u, reset))

    ex = root.get("extra_usage")
    if isinstance(ex, dict) and ex.get("is_enabled") is True and _num(ex.get("utilization")):
        s.has_extra, s.extra_pct = True, float(ex["utilization"])

    lim = root.get("limits")
    if not (s.has_five or s.has_seven) and isinstance(lim, list):
        for it in lim:
            if not isinstance(it, dict) or not _num(it.get("percent")):
                continue
            kind, group = str(it.get("kind") or ""), str(it.get("group") or "")
            p, reset = float(it["percent"]), _iso_to_epoch(it.get("resets_at"))
            if kind == "five_hour" or group == "session":
                s.has_five, s.five_pct, s.five_reset = True, p, reset
            elif kind == "seven_day" or group == "weekly_all":
                s.has_seven, s.seven_pct, s.seven_reset = True, p, reset
            elif group in ("weekly_model", "weekly"):
                sc = it.get("scope")
                name = sc.get("model") if isinstance(sc, dict) and sc.get("model") else kind
                s.models.append(ModelRow(_pretty(str(name)), p, reset))

    known = "five_hour" in root or "seven_day" in root or "limits" in root
    return s if (s.has_five or s.has_seven or known) else None


def _dump_shape(root):
    """Unrecognised response: write only key names (no values, no token) so it can be fixed."""
    try:
        os.makedirs(store.DIR, exist_ok=True)
        lines = ["usage response shape (keys only):"]
        if isinstance(root, dict):
            for k, v in root.items():
                lines.append("  %s : %s" % (k, "{" + ", ".join(v.keys()) + "}" if isinstance(v, dict) else type(v).__name__))
        with open(os.path.join(store.DIR, "api-shape.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _log(line):
    try:
        os.makedirs(store.DIR, exist_ok=True)
        p = os.path.join(store.DIR, "api.log")
        if os.path.exists(p) and os.path.getsize(p) > 60 * 1024:
            os.remove(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (datetime.now().isoformat(timespec="seconds"), line))
    except OSError:
        pass


# ----------------------------------------------------------------- poller

class ApiPoller:
    def __init__(self):
        self.last = None          # last successful Snapshot
        self.last_error = ""
        self.last_status = 0
        self.next_allowed = 0     # epoch: do not query before this
        self.last_poll_at = 0
        self.rate_hits = 0        # consecutive 429 answers (drives the back-off)
        self.running = False
        self._lock = threading.Lock()

    @staticmethod
    def signed_in():
        return token_exists()

    def sign_out(self):
        clear_tokens()
        self.last, self.last_error, self.last_status = None, "", 0

    def poll_async(self, done=None):
        """Runs in a worker thread. done() is called from that thread when finished."""
        if not self.signed_in():
            return False
        with self._lock:
            if self.running:
                return False
            self.running = True

        def work():
            try:
                self._poll()
            except Exception as e:            # never let the thread die silently
                self.last_error = "Beklenmeyen hata: " + type(e).__name__
            finally:
                self.running = False
                if done:
                    done()

        threading.Thread(target=work, daemon=True).start()
        return True

    def _refresh(self, t):
        n, err, dead = refresh(t)
        if n is None:
            _log("refresh failed " + ("dead" if dead else "temp"))
            if dead:
                clear_tokens()
                self.last_error = "Oturum süresi doldu · yeniden giriş yap"
            else:
                self.last_error = "Oturum yenilenemedi: " + err
                self.next_allowed = store.now() + 120
            return None
        save_tokens(n)
        return n

    def _poll(self):
        self.last_poll_at = store.now()
        t = load_tokens()
        if t is None:
            self.last_error = "Oturum anahtarı okunamadı. Yeniden giriş yap."
            return
        if t.expires_at - store.now() < 120 and t.refresh:
            t = self._refresh(t)
            if t is None:
                return
        r = http("GET", USAGE_URL, bearer=t.access)
        if r.status == 401 and t.refresh:
            t = self._refresh(t)
            if t is None:
                return
            r = http("GET", USAGE_URL, bearer=t.access)
        self.last_status = r.status
        _log("usage %d" % r.status)

        if r.status == 429:
            # Obey the server; if it keeps saying 429, back off gradually (90 s, 3 min, 6 min ... max 15 min).
            self.rate_hits += 1
            wait = int(max(r.retry_after, min(900, 90 * 2 ** (self.rate_hits - 1))))
            self.next_allowed = store.now() + wait
            self.last_error = "Sunucu yoğun (429) · %d sn sonra yeniden denenecek" % wait
            return
        self.rate_hits = 0
        if r.status == 0:
            self.last_error, self.next_allowed = "Ağa ulaşılamadı", store.now() + 60
            return
        if r.status != 200:
            self.last_error, self.next_allowed = "claude.ai yanıtı: HTTP %d" % r.status, store.now() + 120
            return

        snap = None
        try:
            root = json.loads(r.body)
            snap = parse_usage(root)
            if snap is None:
                _dump_shape(root)
        except ValueError:
            pass
        if snap is None:
            self.last_error, self.next_allowed = "Yanıt anlaşılamadı (api-shape.txt'ye yazıldı)", store.now() + 300
            return

        self.last_error = ""
        prev, self.last = self.last, snap
        changed = (prev is None or prev.has_five != snap.has_five or prev.has_seven != snap.has_seven
                   or abs(prev.five_pct - snap.five_pct) > 0.001 or abs(prev.seven_pct - snap.seven_pct) > 0.001
                   or snap.updated - prev.updated >= 600)
        if changed:
            store.append_history(snap)
            store.trim_history_if_big()
