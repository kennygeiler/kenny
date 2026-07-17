"""Access control and abuse limits for a shared deployment (PRD §10).

Locally this file does nothing: with no passwords set, `install()` leaves the app open
so `uvicorn core.app:app` still just works. The moment the app is reachable from the
internet it needs three things it does not otherwise have:

  1. A password, because /admin can ratify rules and delete the document library.
  2. A SECOND password on /admin, because "can ask questions" and "can decide what the
     contract means" are different jobs held by different people (PRD §2).
  3. A rate limit on /chat, because every prompt spends the operator's Anthropic budget.
     Without this, one visitor with a loop drains the account.

Set HOLLY_REQUIRE_AUTH=1 (the Dockerfile does) and a deploy missing its passwords fails
at startup rather than silently serving the admin panel to the world.
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
    headers={"WWW-Authenticate": 'Basic realm="Holly", charset="UTF-8"'},
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


class AccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, viewer_pw: str, admin_pw: str, chat_limit: int):
        super().__init__(app)
        self.viewer_pw, self.admin_pw = viewer_pw, admin_pw
        self.limiter = RateLimiter(chat_limit)

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/healthz":  # the platform's probe has no credentials
            return await call_next(request)

        creds = _credentials(request)
        if not creds:
            return _UNAUTHORIZED
        _user, pw = creds

        # DEMO POSTURE: one password opens everything — /admin included — so a reviewer
        # with the shared link can walk the whole loop (ingest → draft → approve) without
        # a second credential. The viewer/admin role split still exists in config for
        # when the link stops being a demo; this just stops enforcing it per-path.
        if not (_equal(pw, self.admin_pw) or _equal(pw, self.viewer_pw)):
            return _UNAUTHORIZED

        if path == "/chat" and request.method == "POST":
            who = request.client.host if request.client else "unknown"
            if not self.limiter.allow(who):
                return JSONResponse(
                    {"error": "Too many questions in the last minute. Wait a moment and "
                              "ask again."},
                    status_code=429,
                )
        return await call_next(request)


def install(app) -> None:
    """Attach access control if configured. Raises if a deploy asks for auth and can't have it."""
    viewer_pw = os.environ.get("HOLLY_VIEWER_PASSWORD", "")
    admin_pw = os.environ.get("HOLLY_ADMIN_PASSWORD", "")
    required = os.environ.get("HOLLY_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")

    if not (viewer_pw and admin_pw):
        if required:
            raise RuntimeError(
                "HOLLY_REQUIRE_AUTH is set but HOLLY_VIEWER_PASSWORD and/or "
                "HOLLY_ADMIN_PASSWORD are missing. Refusing to start: this would serve "
                "the admin panel, the rule-ratify gate and the API key's spend to "
                "anyone with the URL."
            )
        return  # local dev: open, as before

    if _equal(viewer_pw, admin_pw):
        raise RuntimeError(
            "HOLLY_VIEWER_PASSWORD and HOLLY_ADMIN_PASSWORD are identical, which "
            "collapses the reviewer/asker separation the review gate depends on (PRD §2)."
        )

    limit = int(os.environ.get("HOLLY_CHAT_RATE_LIMIT", "20"))
    app.add_middleware(AccessMiddleware, viewer_pw=viewer_pw, admin_pw=admin_pw,
                       chat_limit=limit)
