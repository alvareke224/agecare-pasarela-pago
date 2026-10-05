from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.enums import AdminRole


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    otp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class AdminOut(BaseModel):
    id: UUID
    full_name: str
    email: EmailStr
    role: AdminRole
    mfa_enabled: bool

    model_config = {"from_attributes": True}


class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    admin: AdminOut


class RefreshIn(BaseModel):
    refresh_token: str


class RefreshOut(BaseModel):
    access_token: str
    refresh_token: str


class MeOut(AdminOut):
    permissions: list[str]
    last_login_at: datetime | None


class AdminUserOut(AdminOut):
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class AdminCreateIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    role: AdminRole
    require_mfa: bool | None = None


class AdminCreateOut(BaseModel):
    id: UUID
    email: EmailStr
    role: AdminRole
    invitation_expires_at: datetime


class AdminPatchIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    role: AdminRole | None = None
    is_active: bool | None = None
