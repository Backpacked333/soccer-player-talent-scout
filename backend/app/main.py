"""Private Scoutroom API. Run with uvicorn app.main:app."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app.auth import RateLimiter, password_hash
from app.config import Settings, ensure_local_secret, get_settings
from app.db import init_db


class SecurityBoundary:
    """Reject cross-origin writes before body parsing; bound chunked bodies too."""
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        method = scope["method"]
        path = scope["path"]
        unsafe = method not in {"GET", "HEAD", "OPTIONS"}
        if unsafe and headers.get("origin") not in self.settings.allowed_origins:
            response = JSONResponse({"detail": {"code": "origin_rejected", "message": "This write requires an explicitly permitted browser origin."}}, status_code=403, headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        limit = self.settings.max_upload_bytes + 1024 * 1024 if path.endswith("/upload") and method == "POST" else 256 * 1024
        lengths = headers.getlist("content-length")
        try:
            length = int(lengths[0]) if lengths else 0
            valid = len(lengths) <= 1 and 0 <= length <= limit
        except ValueError:
            valid = False
        if not valid:
            return await JSONResponse({"detail": {"code": "request_too_large", "message": "Request exceeds the configured size limit."}}, status_code=413)(scope, receive, send)
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise HTTPException(413, detail={"code": "request_too_large", "message": "Request exceeds the configured size limit."})
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY")])
                message["headers"] = response_headers
            await send(message)
        return await self.app(scope, limited_receive, secure_send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        secret = ensure_local_secret(settings)
        settings.storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        init_db(settings.database_url)
        application.state.limiter = RateLimiter(secret)
        application.state.dummy_password_hash = password_hash("not-an-account-password")
        yield

    application = FastAPI(title="Scoutroom private API", version="1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.settings = settings
    application.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE", "HEAD"], allow_headers=["Content-Type", "X-CSRF-Token", "Range"], expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"])
    application.add_middleware(SecurityBoundary, settings=settings)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        # Do not reflect submitted passwords, large inputs or exception objects.
        errors = [{"loc": item["loc"], "type": item["type"], "msg": item["msg"]} for item in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @application.exception_handler(IntegrityError)
    async def conflict(_request: Request, _exc: IntegrityError):
        return JSONResponse({"detail": {"code": "conflict", "message": "This change conflicts with existing evidence or another update. Refresh and try again."}}, status_code=409)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, _exc: SQLAlchemyError):
        return JSONResponse({"detail": {"code": "storage_unavailable", "message": "Private storage is temporarily unavailable. Try again later."}}, status_code=503)

    @application.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception):
        return JSONResponse({"detail": {"code": "internal_error", "message": "The request could not be completed safely."}}, status_code=500, headers={"Cache-Control": "no-store"})

    @application.get("/api/health")
    def health():
        return {"status": "ok"}

    from app.routes import accounts, records, media, reporting, intelligence
    from app.intelligence.identity import IdentityError

    @application.exception_handler(IdentityError)
    async def identity_error(_request: Request, exc: IdentityError):
        return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, status_code=exc.status_code)

    for module in (accounts, records, media, reporting, intelligence):
        application.include_router(module.router, prefix="/api")
    return application


app = create_app()
