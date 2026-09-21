from pydantic import BaseModel, EmailStr, Field
from app.models.user import InstanceRoleEnum


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    full_name: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UserAdminUpdate(BaseModel):
    is_active: bool | None = None
    full_name: str | None = None


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    username: str
    full_name: str | None
    is_active: bool
    instance_role: InstanceRoleEnum = InstanceRoleEnum.MEMBER
    auth_provider: str = "local"

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    exp: int
