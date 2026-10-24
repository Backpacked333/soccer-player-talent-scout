"""Authentication routes; signup creates an empty private workspace."""
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import COOKIE_NAME, Principal, api_db, current_user, fail, issue_session, password_hash, password_matches, session_response
from app.models import Account, Workspace
from app.schemas import LoginRequest, SessionResponse, SignupRequest

router = APIRouter()


def throttle(request: Request, email: str, category: str) -> None:
    peer = request.client.host if request.client else "unknown"
    request.app.state.limiter.check(f"{category}-ip", peer, 30)
    request.app.state.limiter.check(f"{category}-email", email, 10)


@router.post("/auth/signup", response_model=SessionResponse, status_code=201)
def signup(data: SignupRequest, request: Request, response: Response, db: Session = Depends(api_db)):
    if not request.app.state.settings.allow_signup:
        fail(403, "signup_disabled", "New account registration is disabled by the operator.")
    throttle(request, data.email, "signup")
    if db.scalar(select(Account.id).where(Account.email == data.email)):
        fail(409, "account_unavailable", "This account cannot be registered. Try signing in instead.")
    workspace = Workspace(name=data.workspace_name)
    db.add(workspace)
    db.flush()
    account = Account(email=data.email, password_hash=password_hash(data.password), workspace_id=workspace.id)
    db.add(account)
    db.flush()
    return issue_session(request, response, db, account, workspace)


@router.post("/auth/login", response_model=SessionResponse)
def login(data: LoginRequest, request: Request, response: Response, db: Session = Depends(api_db)):
    throttle(request, data.email, "login")
    account = db.scalar(select(Account).where(Account.email == data.email, Account.is_active.is_(True)))
    encoded = account.password_hash if account else request.app.state.dummy_password_hash
    valid = password_matches(data.password, encoded)
    if not account or not valid:
        fail(401, "invalid_credentials", "Email or password is incorrect.")
    workspace = db.get(Workspace, account.workspace_id)
    if workspace is None:
        fail(401, "invalid_credentials", "Email or password is incorrect.")
    return issue_session(request, response, db, account, workspace)


@router.get("/auth/me", response_model=SessionResponse)
def me(principal: Principal = Depends(current_user)):
    return session_response(principal)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, principal: Principal = Depends(current_user), db: Session = Depends(api_db)):
    db.delete(principal.session)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/api", secure=request.app.state.settings.cookie_secure, httponly=True, samesite="lax")
