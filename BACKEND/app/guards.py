from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.hospital_profile import HospitalProfile
from app.models.user import User


def ensure_hospital_may_request(db: Session, user: User) -> None:
    """With REQUIRE_VERIFIED_HOSPITALS=true, only admin-verified hospitals may raise requests."""
    if not get_settings().require_verified_hospitals:
        return
    profile = db.query(HospitalProfile).filter(HospitalProfile.user_id == user.id).first()
    if profile is None or not profile.verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your hospital is awaiting verification by an administrator.",
        )
