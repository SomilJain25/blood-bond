from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.audit import write_audit_log
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.auth import OtpSendRequest, OtpSendResponse, OtpVerifyRequest, TokenResponse
from app.schemas.user import UserPublic
from app import sms
from app.security import (
    create_access_token,
    enforce_otp_send_allowed,
    enforce_otp_verify_allowed,
    get_or_create_user,
    issue_otp_challenge,
    normalize_phone,
    register_otp_verify_result,
    verify_otp,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/otp/send", response_model=OtpSendResponse)
def send_otp(
    body: OtpSendRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> OtpSendResponse:
    settings = get_settings()
    phone = normalize_phone(body.phone)
    if len(phone) != 10:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phone must be 10 digits (India)")
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        enforce_otp_send_allowed(db, phone=phone, ip=ip)
    except ValueError as exc:
        write_audit_log(
            db,
            actor_user_id=None,
            action="otp.send.blocked",
            target_type="phone",
            target_id=phone,
            ip=ip,
            user_agent=ua,
            meta={"reason": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    code = issue_otp_challenge(db, settings, phone)
    if sms.is_configured():
        if not sms.send_otp(phone, code):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not send the SMS right now. Please try again.")
    elif not settings.demo_mode:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "SMS provider is not configured")
    write_audit_log(
        db,
        actor_user_id=None,
        action="otp.send",
        target_type="phone",
        target_id=phone,
        ip=ip,
        user_agent=ua,
        meta={"demo_mode": settings.demo_mode},
    )
    return OtpSendResponse(
        demo_otp=code if settings.demo_mode else None,
    )


@router.post("/otp/verify", response_model=TokenResponse)
def verify_otp_route(
    body: OtpVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    settings = get_settings()
    phone = normalize_phone(body.phone)
    if len(phone) != 10:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phone must be 10 digits (India)")
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        enforce_otp_verify_allowed(db, phone=phone, ip=ip)
    except ValueError as exc:
        write_audit_log(
            db,
            actor_user_id=None,
            action="otp.verify.blocked",
            target_type="phone",
            target_id=phone,
            ip=ip,
            user_agent=ua,
            meta={"reason": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    ok = verify_otp(db, settings, phone, body.code)
    register_otp_verify_result(db, phone=phone, ip=ip, ok=ok)
    if not ok:
        write_audit_log(
            db,
            actor_user_id=None,
            action="otp.verify.failed",
            target_type="phone",
            target_id=phone,
            ip=ip,
            user_agent=ua,
            meta={"role": body.role.value if body.role else None},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")
    existing = db.query(User).filter(User.phone == phone).first()
    if existing is not None:
        # Returning user: the account's own role decides, whatever the browser sent
        # (a stale or mis-clicked role must never block a login).
        existing.phone_verified = True
        db.commit()
        db.refresh(existing)
        user = existing
        token = create_access_token(settings, user)
        write_audit_log(
            db,
            actor_user_id=user.id,
            action="otp.verify.success",
            target_type="user",
            target_id=str(user.id),
            ip=ip,
            user_agent=ua,
            meta={"role": user.role.value, "returning": True},
        )
        return TokenResponse(access_token=token, user=UserPublic.model_validate(user))
    if body.role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for this number. Please register first.",
        )
    if body.role == UserRole.admin and phone not in settings.admin_phone_set:
        write_audit_log(
            db,
            actor_user_id=None,
            action="otp.verify.admin_denied",
            target_type="phone",
            target_id=phone,
            ip=ip,
            user_agent=ua,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access is not enabled for this phone")
    try:
        user = get_or_create_user(db, phone, body.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This phone is already registered with a different role",
        ) from exc
    token = create_access_token(settings, user)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="otp.verify.success",
        target_type="user",
        target_id=str(user.id),
        ip=ip,
        user_agent=ua,
        meta={"role": user.role.value},
    )
    return TokenResponse(access_token=token, user=UserPublic.model_validate(user))