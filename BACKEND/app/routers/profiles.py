from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.audit import write_audit_log
from app.database import get_db
from app.deps import get_current_user
from app.models.donor_profile import DonorProfile
from app.models.hospital_profile import HospitalProfile
from app.models.user import User, UserRole
from app.schemas.profile import DonorProfileCreate, HospitalProfileCreate
from app.security import normalize_phone

router = APIRouter(prefix="/profiles", tags=["profiles"])


class DonorAvailabilityUpdate(BaseModel):
    is_available: bool


@router.post("/donor", status_code=status.HTTP_200_OK)
@router.post("/profile/donor", status_code=status.HTTP_200_OK)
def upsert_donor_profile(
    body: DonorProfileCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if user.role != UserRole.donor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only donor accounts can save a donor profile",
        )
    ep = normalize_phone(body.emergency_phone)
    if len(ep) != 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Emergency phone must be 10 digits",
        )
    row = (
        db.query(DonorProfile)
        .filter(DonorProfile.user_id == user.id)
        .first()
    )
    data = body.model_dump()
    data["emergency_phone"] = ep
    if row is None:
        row = DonorProfile(user_id=user.id, **data)
        db.add(row)
    else:
        for k, v in data.items():
            setattr(row, k, v)
    db.commit()
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="profile.donor.upsert",
        target_type="user",
        target_id=str(user.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "saved"}


@router.post("/donor/availability", status_code=status.HTTP_200_OK)
def update_availability(
    body: DonorAvailabilityUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if user.role != UserRole.donor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only donor accounts can update availability",
        )
    row = (
        db.query(DonorProfile)
        .filter(DonorProfile.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Create donor profile before updating availability",
        )
    if row.suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is suspended. Please contact an administrator.",
        )
    row.is_available = bool(body.is_available)
    db.commit()
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="donor.availability.update",
        target_type="user",
        target_id=str(user.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        meta={"is_available": row.is_available},
    )
    return {"status": "updated"}


@router.post("/hospital", status_code=status.HTTP_200_OK)
@router.post("/profile/hospital", status_code=status.HTTP_200_OK)
def upsert_hospital_profile(
    body: HospitalProfileCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if user.role != UserRole.hospital:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only hospital accounts can save a hospital profile",
        )
    facility_name_norm = body.facility_name.strip().lower()
    duplicate = (
        db.query(HospitalProfile)
        .filter(
            func.lower(func.trim(HospitalProfile.facility_name))
            == facility_name_norm
        )
        .filter(HospitalProfile.user_id != user.id)
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hospital/Blood bank name already registered",
        )
    row = (
        db.query(HospitalProfile)
        .filter(HospitalProfile.user_id == user.id)
        .first()
    )
    data = body.model_dump()
    if row is None:
        row = HospitalProfile(user_id=user.id, verified=False, **data)
        db.add(row)
    else:
        for k, v in data.items():
            setattr(row, k, v)
    db.commit()
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="profile.hospital.upsert",
        target_type="user",
        target_id=str(user.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "saved"}
