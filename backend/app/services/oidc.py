"""Minimal OpenID Connect authorization-code flow (Keycloak-compatible)."""
import json
import re
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
from app.models.workspace import Workspace, WorkspaceMember
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


_WORKSPACE_GROUP = re.compile(r"^/openreq/workspaces/([a-z0-9][a-z0-9-]{0,79})/(admin|editor|viewer)$")
_ROLE_RANK = {RoleEnum.VIEWER: 1, RoleEnum.EDITOR: 2, RoleEnum.ADMIN: 3}


def _sync_workspace_groups(db: Session, user: User, claims: dict) -> None:
    """Synchronize memberships declared by Keycloak Group Membership mapper.

    Only records created by this integration are changed or removed. Manual
    memberships remain under OpenReq's control and are never downgraded by SSO.
    Unknown group keys are deliberately ignored: Keycloak must not create
    workspaces as a side effect of a typo or an unexpected group assignment.
    """
    requested: dict[str, RoleEnum] = {}
    for group in claims.get("groups", []) or []:
        if not isinstance(group, str):
            continue
        match = _WORKSPACE_GROUP.match(group)
        if not match:
            continue
        key, role_name = match.groups()
        role = RoleEnum(role_name)
        if key not in requested or _ROLE_RANK[role] > _ROLE_RANK[requested[key]]:
            requested[key] = role

    workspaces = db.query(Workspace).filter(Workspace.access_key.in_(requested.keys())).all() if requested else []
    by_key = {workspace.access_key: workspace for workspace in workspaces}
    active_workspace_ids: set[str] = set()
    for key, role in requested.items():
        workspace = by_key.get(key)
        if not workspace:
            continue
        active_workspace_ids.add(workspace.id)
        member = db.query(WorkspaceMember).filter_by(workspace_id=workspace.id, user_id=user.id).first()
        if member is None:
            db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role, auth_source="oidc"))
        elif member.auth_source == "oidc":
            member.role = role

    oidc_memberships = db.query(WorkspaceMember).filter_by(user_id=user.id, auth_source="oidc").all()
    for member in oidc_memberships:
        if member.workspace_id not in active_workspace_ids:
            db.delete(member)


def _ensure_first_admin_workspace(db: Session, user: User) -> None:
    """Give the first OIDC instance admin a usable workspace on a fresh install.

    A password-less deployment has no setup wizard, so without this bootstrap an
    administrator would be stranded on the empty workspace selector.
    """
    if user.instance_role != InstanceRoleEnum.INSTANCE_ADMIN:
        return
    has_membership = db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user.id).first()
    if has_membership:
        return
    workspace = Workspace(name="Default Workspace", access_key="default-workspace", description="Created for the first OIDC administrator")
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=RoleEnum.ADMIN))


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
        # Keycloak may include an at_hash claim in the ID token. python-jose
        # validates it only when the access token from the same code exchange is
        # supplied explicitly.
        claims = jwt.decode(
            token_data["id_token"], jwks(), algorithms=["RS256", "ES256"],
            audience=settings.OIDC_CLIENT_ID, issuer=settings.OIDC_ISSUER_URL,
            access_token=token_data.get("access_token"),
        )
        # Keycloak client roles are normally present in the access token, not
        # necessarily in the ID token. Verify the access token independently
        # before using it for authorization decisions. Keycloak may omit this
        # client from aud while still recording it as azp, hence audience is
        # intentionally not enforced here; azp is checked below instead.
        access_claims = jwt.decode(
            token_data["access_token"], jwks(), algorithms=["RS256", "ES256"],
            issuer=settings.OIDC_ISSUER_URL, options={"verify_aud": False},
        )
        if access_claims.get("azp") != settings.OIDC_CLIENT_ID:
            raise HTTPException(status_code=401, detail="OIDC access token was not issued for this client")
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

    roles = keycloak_roles(access_claims)
    user.instance_role = _mapped_instance_role(roles)
    _sync_workspace_role(db, user, roles)
    _sync_workspace_groups(db, user, access_claims)
    _ensure_first_admin_workspace(db, user)
    db.commit()
    return create_access_token(subject=user.id)
