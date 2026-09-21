from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode
import secrets
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.core.security import hash_password, verify_password, create_access_token
from app.database import get_db
from app.models.user import User
from app.models.user import InstanceRoleEnum
from app.services import oidc
from app.schemas.user import UserCreate, UserLogin, UserOut, Token

router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if not settings.LOCAL_AUTH_ENABLED:
        raise HTTPException(status_code=403, detail="Local registration is disabled")
    if not settings.ALLOW_REGISTRATION and db.query(User).count() > 0:
        raise HTTPException(status_code=403, detail="Registration disabled")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        instance_role=InstanceRoleEnum.ADMIN if db.query(User).count() == 0 else InstanceRoleEnum.MEMBER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    if not settings.LOCAL_AUTH_ENABLED:
        raise HTTPException(status_code=403, detail="Local login is disabled")
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    token = create_access_token(subject=user.id)
    return Token(access_token=token)


@router.post("/refresh", response_model=Token)
def refresh_token(current_user: User = Depends(get_current_user)):
    """Issue a fresh token for an authenticated user."""
    token = create_access_token(subject=current_user.id)
    return Token(access_token=token)


@router.get("/oidc/config")
def oidc_config():
    return {"enabled": oidc.is_enabled(), "local_enabled": settings.LOCAL_AUTH_ENABLED}


@router.get("/oidc/login")
def oidc_login(request: Request):
    url, state = oidc.login_url(request)
    response = RedirectResponse(url)
    response.set_cookie("openreq_oidc_state", state, httponly=True, secure=request.url.scheme == "https", samesite="lax", max_age=600)
    return response


@router.get("/oidc/callback", name="oidc_callback")
def oidc_callback(request: Request, code: str, state: str, db: Session = Depends(get_db)):
    expected = request.cookies.get("openreq_oidc_state")
    if not expected or not secrets.compare_digest(expected, state):
        raise HTTPException(status_code=400, detail="Invalid OIDC state")
    token = oidc.finish_login(request, db, code)
    target = (settings.OIDC_FRONTEND_URL or str(request.base_url)).rstrip("/") + "/oidc/callback?" + urlencode({"token": token})
    response = RedirectResponse(target)
    response.delete_cookie("openreq_oidc_state")
    return response
