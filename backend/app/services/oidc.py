"""Minimal OpenID Connect authorization-code flow (Keycloak-compatible)."""
import json
import secrets
from functools import lru_cache
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import create_access_token, hash_password
from app.models.user import InstanceRoleEnum, User
from app.models.workspace import WorkspaceMember
from app.models.user import RoleEnum


def is_enabled() -> bool:
    return bool(settings.OIDC_ISSUER_URL and settings.OIDC_CLIENT_ID)


@lru_cache(maxsize=1)
def discovery() -> dict:
    if not is_enabled():
        raise HTTPException(status_code=404, detail="OIDC is not configured")
    url = settings.OIDC_ISSUER_URL.rstrip("/") + "/.well-known/openid-configuration"
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OIDC discovery failed: {exc}") from exc


@lru_cache(maxsize=1)
def jwks() -> dict:
    try:
        response = httpx.get(discovery()["jwks_uri"], timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OIDC key discovery failed: {exc}") from exc


def callback_url(request: Request) -> str:
    return settings.OIDC_REDIRECT_URI or str(request.url_for("oidc_callback"))


def _role_map(raw: str) -> dict[str, str]:
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        return {}


def keycloak_roles(claims: dict) -> set[str]:
    """Return roles for OpenReq's Keycloak client only.

    Keycloak places client roles in ``resource_access.<client-id>.roles``.
    Scoping this lookup prevents a role assigned for an unrelated client from
    becoming an OpenReq administrator.
    """
    client_id = settings.OIDC_ROLE_CLIENT_ID or settings.OIDC_CLIENT_ID
    client = (claims.get("resource_access", {}) or {}).get(client_id or "", {})
    return set(client.get("roles", []) or [])


def _mapped_instance_role(roles: set[str]) -> InstanceRoleEnum:
    mapped = {_role_map(settings.OIDC_INSTANCE_ROLE_MAP).get(role) for role in roles}
    return InstanceRoleEnum.INSTANCE_ADMIN if "instance_admin" in mapped else InstanceRoleEnum.MEMBER


def _unique_username(db: Session, preferred: str) -> str:
    base = "".join(ch for ch in preferred if ch.isalnum() or ch in "._-")[:90] or "oidc-user"
    candidate = base
    index = 2
    while db.query(User).filter(User.username == candidate).first():
        candidate = f"{base[:90]}-{index}"
        index += 1
    return candidate


def _sync_workspace_role(db: Session, user: User, roles: set[str]) -> None:
    workspace_id = settings.OIDC_DEFAULT_WORKSPACE_ID
    if not workspace_id:
        return
    mapping = _role_map(settings.OIDC_WORKSPACE_ROLE_MAP)
    values = [mapping[role] for role in roles if mapping.get(role) in {r.value for r in RoleEnum}]
    if not values:
        return
    desired = max((RoleEnum(value) for value in values), key=lambda role: {RoleEnum.VIEWER: 1, RoleEnum.EDITOR: 2, RoleEnum.ADMIN: 3}[role])
    member = db.query(WorkspaceMember).filter_by(workspace_id=workspace_id, user_id=user.id).first()
    if member:
        member.role = desired
    else:
        db.add(WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role=desired))


def login_url(request: Request) -> tuple[str, str]:
    metadata = discovery()
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": callback_url(request),
        "state": state,
    }
    return metadata["authorization_endpoint"] + "?" + urlencode(params), state


def finish_login(request: Request, db: Session, code: str) -> str:
    metadata = discovery()
    body = {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": callback_url(request), "client_id": settings.OIDC_CLIENT_ID,
    }
    if settings.OIDC_CLIENT_SECRET:
        body["client_secret"] = settings.OIDC_CLIENT_SECRET
    try:
        response = httpx.post(metadata["token_endpoint"], data=body, timeout=15)
        response.raise_for_status()
        token_data = response.json()
        claims = jwt.decode(token_data["id_token"], jwks(), algorithms=["RS256", "ES256"], audience=settings.OIDC_CLIENT_ID, issuer=settings.OIDC_ISSUER_URL)
    except (KeyError, JWTError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=401, detail=f"OIDC authentication failed: {exc}") from exc

    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise HTTPException(status_code=400, detail="OIDC provider must return subject and email claims")
    issuer = settings.OIDC_ISSUER_URL.rstrip("/")
    user = db.query(User).filter(User.oidc_issuer == issuer, User.oidc_subject == subject).first()
    if not user:
        # Do not silently attach an external identity to a local account by email.
        user = User(email=email, username=_unique_username(db, claims.get("preferred_username") or email.split("@")[0]),
                    full_name=claims.get("name"), hashed_password=hash_password(secrets.token_urlsafe(48)),
                    auth_provider="oidc", oidc_issuer=issuer, oidc_subject=subject)
        db.add(user)
        db.flush()
    elif not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    else:
        user.email = email
        user.full_name = claims.get("name") or user.full_name

    roles = keycloak_roles(claims)
    user.instance_role = _mapped_instance_role(roles)
    _sync_workspace_role(db, user, roles)
    db.commit()
    return create_access_token(subject=user.id)
