"""Access control and abuse limits for a shared deployment (PRD §10).

Locally this file does nothing: with no passwords set, `install()` leaves the app open
so `uvicorn core.app:app` still just works. The moment the app is reachable from the
internet it needs four things it does not otherwise have:

  1. A password, because /admin can ratify rules and delete the document library.
  2. A SECOND password on /admin — ENFORCED per-path (TICKETS.md C1): "can ask
     questions" and "can decide what the contract means" are different jobs held by
     different people (PRD §2). The viewer credential opens chat and documents; only
     the admin credential opens /admin/*.
  3. A rate limit on /chat, because every prompt spends the operator's Anthropic
     budget, plus a throttle on FAILED logins (TICKETS.md C3) so the passwords can't
     be guessed online at full speed.
  4. An Origin check on state-changing requests (TICKETS.md C2): browsers attach
     cached Basic credentials to cross-site form POSTs, so without this a hostile page
     could replace a contract PDF via /admin/upload using the admin's own browser.

Set KENNY_REQUIRE_AUTH=1 (the Dockerfile does) and a deploy missing its passwords or
its ledger HMAC key fails at startup rather than silently serving the admin panel to
the world.
"""
from __future__ import annotations

import base64
import hmac
import os
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

_UNAUTHORIZED = Response(
    status_code=401,
    content="Authentication required.",
    headers={"WWW-Authenticate": 'Basic realm="Kenny", charset="UTF-8"'},
)


def _equal(a: str, b: str) -> bool:
    # Constant-time: a plain == leaks the password one character at a time via timing.
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _credentials(request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        user, _, pw = base64.b64decode(token).decode("utf-8").partition(":")
    except Exception:
        return None
    return user, pw


def _client_ip(request) -> str:
    """The real client, not the proxy (TICKETS.md C4).

    On Fly every connection arrives from the proxy, so request.client.host is the
    proxy's address and 'per-IP' limits collapse into one shared bucket — one runaway
    visitor 429s everyone. Fly-Client-IP is trusted ONLY when we are actually on Fly
    (FLY_APP_NAME is set by the platform); locally a spoofable header must not let a
    client choose its own bucket.
    """
    if os.environ.get("FLY_APP_NAME"):
        fly = request.headers.get("fly-client-ip", "").strip()
        if fly:
            return fly
    return request.client.host if request.client else "unknown"


def _origin_ok(request) -> bool:
    """Reject state-changing requests whose Origin names ANOTHER site (CSRF).

    Browsers send Origin on every cross-site POST (including the text/plain form
    trick), so a hostile page cannot avoid presenting one — and cannot forge ours.
    A missing Origin means a non-browser client (curl, tests); those don't carry
    ambient credentials, which is the thing CSRF exploits, so they pass.
    """
    origin = request.headers.get("origin", "").strip()
    if not origin:
        return True
    if origin.lower() == "null":            # sandboxed-iframe / data: URL attacker
        return False
    host = request.headers.get("host", "")
    _, _, origin_host = origin.partition("://")
    return origin_host.lower() == host.lower()


class RateLimiter:
    """Fixed-window per-IP counter. Cheap, in-process, and enough for a shared demo.

    Deliberately not a distributed limiter: this exists to cap API spend and stop a
    runaway loop, not to survive a determined attacker. Multi-worker or multi-instance
    needs Redis (PRD §8) — with N workers the effective limit is N x the configured one.
    """

    def __init__(self, limit: int, window_s: int = 60):
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window_s]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 2048:  # bound memory; drop windows that have expired
                self._hits = {k: v for k, v in self._hits.items()
                              if v and now - v[-1] < self.window_s}
            return True


class FailureThrottle:
    """Exponential backoff on FAILED authentication, per client (TICKETS.md C3).

    The deploy script generates 144-bit passwords, but DEPLOY.md lets an operator set
    a memorable one by hand — and an unthrottled Basic-auth prompt is an online oracle.
    After `free` misses, each further miss doubles a lockout (capped), and successes
    clear it. In-process state is enough for the single-worker demo posture.
    """

    def __init__(self, free: int = 5, base_s: float = 2.0, cap_s: float = 300.0):
        self.free, self.base_s, self.cap_s = free, base_s, cap_s
        self._misses: dict[str, tuple[int, float]] = {}   # key -> (count, locked_until)
        self._lock = threading.Lock()

    def locked_for(self, key: str) -> float:
        with self._lock:
            _, until = self._misses.get(key, (0, 0.0))
            return max(0.0, until - time.time())

    def record_failure(self, key: str) -> int:
        with self._lock:
            count, _ = self._misses.get(key, (0, 0.0))
            count += 1
            delay = 0.0
            if count > self.free:
                delay = min(self.cap_s, self.base_s * (2 ** (count - self.free - 1)))
            self._misses[key] = (count, time.time() + delay)
            if len(self._misses) > 4096:
                now = time.time()
                self._misses = {k: v for k, v in self._misses.items()
                                if v[1] > now or v[0] > self.free}
            return count

    def reset(self, key: str) -> None:
        with self._lock:
            self._misses.pop(key, None)


class RateLimitOnlyMiddleware(BaseHTTPMiddleware):
    """Open mode: no passwords anywhere, every page serves without credentials — but
    POST /chat still spends the operator's Anthropic budget, so the per-client rate
    limit stays on. Removing auth must never mean removing the spend cap."""

    def __init__(self, app, chat_limit: int):
        super().__init__(app)
        self.limiter = RateLimiter(chat_limit)

    async def dispatch(self, request, call_next):
        if request.url.path == "/chat" and request.method == "POST":
            if not self.limiter.allow(_client_ip(request)):
                return JSONResponse(
                    {"error": "Too many questions in the last minute. Wait a moment "
                              "and ask again."}, status_code=429)
        return await call_next(request)


class AccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, viewer_pw: str, admin_pw: str, chat_limit: int,
                 ledger_factory=None):
        super().__init__(app)
        self.viewer_pw, self.admin_pw = viewer_pw, admin_pw
        self.limiter = RateLimiter(chat_limit)
        self.failures = FailureThrottle()
        self._ledger_factory = ledger_factory

    def _ledger(self, type_: str, payload: dict) -> None:
        """Best-effort audit of auth events — a ledger problem must never take the
        auth path down with it."""
        if self._ledger_factory is None:
            return
        try:
            self._ledger_factory().append(type_, payload, actor="auth")
        except Exception:
            pass

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/healthz":  # the platform's probe has no credentials
            return await call_next(request)

        ip = _client_ip(request)

        remaining = self.failures.locked_for(ip)
        if remaining > 0:
            return JSONResponse(
                {"error": "Too many failed sign-in attempts. Wait and try again."},
                status_code=429, headers={"Retry-After": str(int(remaining) + 1)})

        creds = _credentials(request)
        if not creds:
            return _UNAUTHORIZED
        _user, pw = creds

        is_admin = _equal(pw, self.admin_pw)
        is_viewer = _equal(pw, self.viewer_pw)
        if not (is_admin or is_viewer):
            count = self.failures.record_failure(ip)
            # Ledger the first miss past the free allowance and every 10th after —
            # the trail must show a guessing campaign without letting the campaign
            # itself flood the ledger.
            if count == self.failures.free + 1 or count % 10 == 0:
                self._ledger("auth.failed", {"ip": ip, "path": path, "count": count})
            return _UNAUTHORIZED
        self.failures.reset(ip)

        # ROLE SPLIT (TICKETS.md C1): /admin/* — the ratify gate, uploads, ingest, the
        # full ledger — requires the ADMIN credential. The viewer password opens chat
        # and the documents it cites, exactly what DEPLOY.md promises when it says
        # "share the viewer password".
        if path == "/admin" or path.startswith("/admin/"):
            if not is_admin:
                self._ledger("auth.denied",
                             {"ip": ip, "path": path, "role": "viewer"})
                return JSONResponse(
                    {"error": "The admin surface needs the admin password — the "
                              "viewer credential only opens chat."}, status_code=403)

        # CSRF (TICKETS.md C2): a state-changing request from another origin rides the
        # browser's cached credentials, not the user's intent.
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(request):
            self._ledger("auth.csrf_blocked",
                         {"ip": ip, "path": path,
                          "origin": request.headers.get("origin", "")})
            return JSONResponse({"error": "cross-origin request refused"},
                                status_code=403)

        if path == "/chat" and request.method == "POST":
            if not self.limiter.allow(ip):
                return JSONResponse(
                    {"error": "Too many questions in the last minute. Wait a moment and "
                              "ask again."},
                    status_code=429,
                )
        return await call_next(request)


def install(app, ledger_factory=None) -> None:
    """Attach access control if configured. Raises if a deploy asks for auth and can't have it."""
    viewer_pw = os.environ.get("KENNY_VIEWER_PASSWORD", "")
    admin_pw = os.environ.get("KENNY_ADMIN_PASSWORD", "")
    required = os.environ.get("KENNY_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")
    limit = int(os.environ.get("KENNY_CHAT_RATE_LIMIT", "20"))

    if not (viewer_pw and admin_pw):
        if required:
            raise RuntimeError(
                "KENNY_REQUIRE_AUTH is set but KENNY_VIEWER_PASSWORD and/or "
                "KENNY_ADMIN_PASSWORD are missing. Refusing to start: this would serve "
                "the admin panel, the rule-ratify gate and the API key's spend to "
                "anyone with the URL."
            )
        # OPEN mode (the default): no sign-in anywhere. The chat spend cap stays.
        app.add_middleware(RateLimitOnlyMiddleware, chat_limit=limit)
        return

    if required and not os.environ.get("KENNY_LEDGER_KEY", "").strip():
        raise RuntimeError(
            "KENNY_REQUIRE_AUTH is set but KENNY_LEDGER_KEY is missing. Refusing to "
            "start: without the HMAC key the audit ledger is only internally "
            "consistent — anyone with volume access could rewrite it and verify() "
            "would still pass. Set KENNY_LEDGER_KEY in the platform's secret store "
            "(never on the data volume)."
        )

    if _equal(viewer_pw, admin_pw):
        raise RuntimeError(
            "KENNY_VIEWER_PASSWORD and KENNY_ADMIN_PASSWORD are identical, which "
            "collapses the reviewer/asker separation the review gate depends on (PRD §2)."
        )

    app.add_middleware(AccessMiddleware, viewer_pw=viewer_pw, admin_pw=admin_pw,
                       chat_limit=limit, ledger_factory=ledger_factory)
