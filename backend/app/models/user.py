import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

import enum


class RoleEnum(str, enum.Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class InstanceRoleEnum(str, enum.Enum):
    INSTANCE_ADMIN = "instance_admin"
    MEMBER = "member"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True)
    instance_role: Mapped[InstanceRoleEnum] = mapped_column(
        SAEnum(InstanceRoleEnum, values_callable=lambda enum_cls: [item.value for item in enum_cls]),
        default=InstanceRoleEnum.MEMBER,
    )
    auth_provider: Mapped[str] = mapped_column(String(30), default="local")
    oidc_issuer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    collections: Mapped[list["Collection"]] = relationship(back_populates="owner")  # noqa: F821
