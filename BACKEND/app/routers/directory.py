from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.donor_profile import DonorProfile
from app.models.hospital_profile import HospitalProfile
from app.deps import get_current_user
from app.models.user import User, UserRole
from app.schemas.profile import DonorPublic, HospitalPublic
from app.utils.geofence import is_within_geofence

router = APIRouter(tags=["directory"])


@router.get("/donors", response_model=list[DonorPublic])
def list_donors(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    blood_group: str | None = Query(None, max_length=5),
    near: str | None = Query(None, max_length=300),
    geofence_km: float = Query(25.0, gt=0, le=500),
) -> list[DonorPublic]:
    if user.role not in (UserRole.hospital, UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hospital or admin role required")
    q = (
        db.query(DonorProfile)
        .join(User)
        .filter(DonorProfile.consent_share.is_(True))
        .filter(DonorProfile.suspended.is_(False))
        .filter(User.phone_verified.is_(True))
    )
    if blood_group:
        q = q.filter(DonorProfile.blood_group == blood_group)
    rows = q.all()
    today = date.today()
    eligible_rows = [
        r
        for r in rows
        if r.last_donation_date is None or (today - r.last_donation_date).days >= 56
    ]
    if near:
        eligible_rows = [
            r
            for r in eligible_rows
            if is_within_geofence(
                center_location=near,
                target_location=r.area_text,
                radius_km=geofence_km,
            )
        ]
    return [DonorPublic.model_validate(r) for r in eligible_rows]


@router.get("/hospitals", response_model=list[HospitalPublic])
def list_hospitals(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[HospitalPublic]:
    rows = db.query(HospitalProfile).all()
    return [HospitalPublic.model_validate(r) for r in rows]
