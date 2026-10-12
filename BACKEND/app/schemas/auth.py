from pydantic import BaseModel, Field, field_validator

from app.models.user import UserRole
from app.sanitization import sanitize_text
from app.schemas.user import UserPublic


class OtpSendRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=15)

    @field_validator("phone")
    @classmethod
    def sanitize_phone(cls, value: str) -> str:
        return sanitize_text(value)


class OtpSendResponse(BaseModel):
    ok: bool = True
    expires_in_seconds: int = 600
    demo_otp: str | None = None


class OtpVerifyRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=15)
    code: str = Field(..., min_length=6, max_length=6)
    # Only used when this phone has no account yet. Existing accounts always keep their stored role.
    role: UserRole | None = None

    @field_validator("phone", "code")
    @classmethod
    def sanitize_inputs(cls, value: str) -> str:
        return sanitize_text(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic