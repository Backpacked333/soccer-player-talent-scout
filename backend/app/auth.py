"""Opaque, revocable workspace sessions. No credentials are returned or logged."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import NoReturn

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_session_factory, utcnow
from app.models import Account, AuthSession, Workspace
from app.schemas import AccountRead, SessionResponse, WorkspaceRead

COOKIE_NAME = "scout_session"
PASSWORD_SLOTS = threading.BoundedSemaphore(4)


def fail(status: int, code: str, message: str) -> NoReturn:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def api_db(request: Request):
    with get_session_factory(request.app.state.settings.database_url)() as db:
        try:
            yield db
        except Exception:
            db.rollback()
            raise


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    if not PASSWORD_SLOTS.acquire(blocking=False):
        fail(429, "authentication_busy", "Authentication is busy. Try again shortly.")
    try:
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024**2)
        return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"
    finally:
        PASSWORD_SLOTS.release()


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, _digest = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        return hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RateLimiter:
    """Bounded per-process guard; production also needs ingress rate limits.

    Peer IPs come from the ASGI server, never parsed here from headers. Operators
    must disable proxy headers or trust only a sanitizing ingress. Account keys
    are HMACs; this short-lived memory store contains no emails.
    """
    def __init__(self, secret: str):
        self.secret = secret.encode()
        self.buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = threading.Lock()

    def check(self, category: str, value: str, limit: int, window: int = 900) -> None:
        key = hmac.new(self.secret, f"{category}:{value}".encode(), hashlib.sha256).hexdigest()
        now = time.monotonic()
        with self.lock:
            bucket = self.buckets.pop(key, deque())
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            self.buckets[key] = bucket
            if len(self.buckets) > 10000:
                self.buckets.popitem(last=False)
            if len(bucket) >= limit:
                raise HTTPException(429, detail={"code": "rate_limited", "message": "Too many attempts. Try again later."}, headers={"Retry-After": str(window)})
            bucket.append(now)


@dataclass(frozen=True)
class Principal:
    account: Account
    workspace: Workspace
    session: AuthSession

    @property
    def workspace_id(self) -> str:
        return self.workspace.id


def current_user(request: Request, db: Session = Depends(api_db)) -> Principal:
    token = request.cookies.get(COOKIE_NAME, "")
    if not 32 <= len(token) <= 128:
        fail(401, "authentication_required", "Sign in to access this private workspace.")
    session = db.get(AuthSession, token_hash(token))
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
        fail(401, "authentication_required", "Your session has expired. Sign in again.")
    account = db.scalar(select(Account).where(Account.id == session.account_id, Account.workspace_id == session.workspace_id, Account.is_active.is_(True)))
    workspace = db.get(Workspace, session.workspace_id)
    if account is None or workspace is None:
        fail(401, "authentication_required", "Sign in to access this private workspace.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if len(supplied) > 128 or not supplied.isascii() or not hmac.compare_digest(supplied, session.csrf_token):
            fail(403, "csrf_rejected", "Refresh your session and try again.")
    return Principal(account, workspace, session)


def session_response(principal: Principal) -> SessionResponse:
    return SessionResponse(account=AccountRead.model_validate(principal.account), workspace=WorkspaceRead.model_validate(principal.workspace), csrf_token=principal.session.csrf_token)


def issue_session(request: Request, response: Response, db: Session, account: Account, workspace: Workspace) -> SessionResponse:
    settings = request.app.state.settings
    previous = request.cookies.get(COOKIE_NAME, "")
    if 32 <= len(previous) <= 128:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(previous)))
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    existing = list(db.scalars(select(AuthSession).where(AuthSession.account_id == account.id).order_by(AuthSession.created_at.desc())))
    for stale in existing[9:]:
        db.delete(stale)
    raw = secrets.token_urlsafe(48)
    session = AuthSession(token_hash=token_hash(raw), workspace_id=workspace.id, account_id=account.id, csrf_token=secrets.token_urlsafe(32), expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours))
    db.add(session)
    db.commit()
    response.set_cookie(COOKIE_NAME, raw, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_ttl_hours * 3600, path="/api")
    response.headers["Cache-Control"] = "no-store"
    return session_response(Principal(account, workspace, session))
