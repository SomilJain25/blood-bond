import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.otp_challenge import OtpChallenge
from app.models.otp_rate_limit import OtpKeyType, OtpRateLimit
from app.models.user import User, UserRole


def normalize_phone(raw: str) -> str:
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def hash_otp(settings: Settings, phone: str, code: str) -> str:
    msg = f"{phone}:{code}".encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()


OTP_RESEND_COOLDOWN_SECONDS = 30
OTP_SEND_WINDOW_SECONDS = 10 * 60
OTP_MAX_SENDS_PER_PHONE_WINDOW = 5
OTP_MAX_SENDS_PER_IP_WINDOW = 20

OTP_FAIL_WINDOW_SECONDS = 10 * 60
OTP_MAX_FAILS_PER_PHONE_WINDOW = 5
OTP_MAX_FAILS_PER_IP_WINDOW = 20
OTP_LOCKOUT_SECONDS = 10 * 60


def _get_or_create_rl(db: Session, *, key_type: OtpKeyType, key: str) -> OtpRateLimit:
    row = (
        db.query(OtpRateLimit)
        .filter(OtpRateLimit.key_type == key_type, OtpRateLimit.key == key)
        .first()
    )
    if row is not None:
        return row
    row = OtpRateLimit(key_type=key_type, key=key)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC so comparisons work."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _reset_if_window_expired(now: datetime, started_at: datetime, window_seconds: int) -> bool:
    return (now - _aware(started_at)).total_seconds() >= window_seconds


def enforce_otp_send_allowed(db: Session, *, phone: str, ip: str | None) -> None:
    now = datetime.now(timezone.utc)

    phone_rl = _get_or_create_rl(db, key_type=OtpKeyType.phone, key=phone)
    if _aware(phone_rl.locked_until) and _aware(phone_rl.locked_until) > now:
        raise ValueError("OTP temporarily locked due to failed attempts. Try later.")
    if _aware(phone_rl.resend_available_at) and _aware(phone_rl.resend_available_at) > now:
        remaining = int((_aware(phone_rl.resend_available_at) - now).total_seconds())
        raise ValueError(f"Please wait {remaining}s before resending OTP")

    if _reset_if_window_expired(now, phone_rl.send_window_started_at, OTP_SEND_WINDOW_SECONDS):
        phone_rl.send_window_started_at = now
        phone_rl.send_count_in_window = 0

    if phone_rl.send_count_in_window >= OTP_MAX_SENDS_PER_PHONE_WINDOW:
        raise ValueError("OTP send rate limit exceeded for this phone. Try later.")

    if ip:
        ip_rl = _get_or_create_rl(db, key_type=OtpKeyType.ip, key=ip)
        if _reset_if_window_expired(now, ip_rl.send_window_started_at, OTP_SEND_WINDOW_SECONDS):
            ip_rl.send_window_started_at = now
            ip_rl.send_count_in_window = 0
        if ip_rl.send_count_in_window >= OTP_MAX_SENDS_PER_IP_WINDOW:
            raise ValueError("OTP send rate limit exceeded for this IP. Try later.")
        ip_rl.send_count_in_window += 1

    phone_rl.send_count_in_window += 1
    phone_rl.resend_available_at = now + timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS)
    db.commit()


def register_otp_verify_result(db: Session, *, phone: str, ip: str | None, ok: bool) -> None:
    now = datetime.now(timezone.utc)
    phone_rl = _get_or_create_rl(db, key_type=OtpKeyType.phone, key=phone)

    if _aware(phone_rl.locked_until) and _aware(phone_rl.locked_until) > now:
        return

    if _reset_if_window_expired(now, phone_rl.fail_window_started_at, OTP_FAIL_WINDOW_SECONDS):
        phone_rl.fail_window_started_at = now
        phone_rl.failed_verifies_in_window = 0

    if ok:
        phone_rl.failed_verifies_in_window = 0
        if ip:
            ip_rl = _get_or_create_rl(db, key_type=OtpKeyType.ip, key=ip)
            ip_rl.failed_verifies_in_window = 0
        db.commit()
        return

    phone_rl.failed_verifies_in_window += 1
    if phone_rl.failed_verifies_in_window >= OTP_MAX_FAILS_PER_PHONE_WINDOW:
        phone_rl.locked_until = now + timedelta(seconds=OTP_LOCKOUT_SECONDS)
    if ip:
        ip_rl = _get_or_create_rl(db, key_type=OtpKeyType.ip, key=ip)
        if _reset_if_window_expired(now, ip_rl.fail_window_started_at, OTP_FAIL_WINDOW_SECONDS):
            ip_rl.fail_window_started_at = now
            ip_rl.failed_verifies_in_window = 0
        ip_rl.failed_verifies_in_window += 1
        if ip_rl.failed_verifies_in_window >= OTP_MAX_FAILS_PER_IP_WINDOW:
            ip_rl.locked_until = now + timedelta(seconds=OTP_LOCKOUT_SECONDS)
    db.commit()


def enforce_otp_verify_allowed(db: Session, *, phone: str, ip: str | None) -> None:
    now = datetime.now(timezone.utc)
    phone_rl = _get_or_create_rl(db, key_type=OtpKeyType.phone, key=phone)
    if _aware(phone_rl.locked_until) and _aware(phone_rl.locked_until) > now:
        remaining = int((_aware(phone_rl.locked_until) - now).total_seconds())
        raise ValueError(f"Too many failed attempts. Try again in {remaining}s")
    if ip:
        ip_rl = _get_or_create_rl(db, key_type=OtpKeyType.ip, key=ip)
        if _aware(ip_rl.locked_until) and _aware(ip_rl.locked_until) > now:
            remaining = int((_aware(ip_rl.locked_until) - now).total_seconds())
            raise ValueError(f"Too many failed attempts from this IP. Try again in {remaining}s")


def issue_otp_challenge(db: Session, settings: Settings, phone: str) -> str:
    if settings.demo_mode:
        code = settings.demo_otp_code
    else:
        code = f"{secrets.randbelow(1_000_000):06d}"

    db.query(OtpChallenge).filter(OtpChallenge.phone == phone).delete()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    row = OtpChallenge(phone=phone, code_hash=hash_otp(settings, phone, code), expires_at=expires)
    db.add(row)
    db.commit()
    return code


def verify_otp(db: Session, settings: Settings, phone: str, code: str) -> bool:
    now = datetime.now(timezone.utc)
    row = (
        db.query(OtpChallenge)
        .filter(OtpChallenge.phone == phone, OtpChallenge.expires_at > now)
        .order_by(OtpChallenge.created_at.desc())
        .first()
    )
    if not row:
        return False
    expected = hash_otp(settings, phone, code)
    if not hmac.compare_digest(row.code_hash, expected):
        return False
    db.query(OtpChallenge).filter(OtpChallenge.phone == phone).delete()
    db.commit()
    return True


def create_access_token(settings: Settings, user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user.id),
        "phone": user.phone,
        "role": user.role.value,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(settings: Settings, token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def get_user_from_token(db: Session, settings: Settings, token: str) -> User | None:
    try:
        payload = decode_token(settings, token)
        sub = payload.get("sub")
        token_phone = payload.get("phone")
        token_role = payload.get("role")
        if not sub:
            return None
        user = db.query(User).filter(User.id == uuid.UUID(str(sub))).first()
        if user is None:
            return None
        if token_phone != user.phone:
            return None
        if token_role != user.role.value:
            return None
        if not user.phone_verified:
            return None
        return user
    except (JWTError, ValueError):
        return None


def get_or_create_user(db: Session, phone: str, role: UserRole) -> User:
    user = db.query(User).filter(User.phone == phone).first()
    if user:
        if user.role != role:
            raise ValueError("Role mismatch for existing account")
        user.phone_verified = True
        db.commit()
        db.refresh(user)
        return user
    user = User(phone=phone, role=role, phone_verified=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
