from functools import wraps

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import RoleEnum, InstanceRoleEnum, User
from app.models.workspace import WorkspaceMember


ROLE_HIERARCHY = {
    RoleEnum.ADMIN: 3,
    RoleEnum.EDITOR: 2,
    RoleEnum.VIEWER: 1,
}


def check_workspace_role(
    db: Session, user_id: str, workspace_id: str, minimum_role: RoleEnum
) -> WorkspaceMember:
    member = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this workspace",
        )
    if ROLE_HIERARCHY[member.role] < ROLE_HIERARCHY[minimum_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires at least {minimum_role.value} role",
        )
    return member


def require_instance_admin(current_user: User) -> User:
    if current_user.instance_role != InstanceRoleEnum.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requires instance administrator role")
    return current_user
