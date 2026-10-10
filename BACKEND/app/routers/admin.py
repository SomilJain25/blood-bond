from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import sms
from app.audit import write_audit_log
from app.database import get_db
from app.deps import get_current_user
from app.matching import is_donor_eligible
from app.models.audit_log import AuditLog
from app.models.donor_profile import DonorProfile
from app.models.hospital_profile import HospitalProfile
from app.models.request import BloodRequest, RequestStatus
from app.models.user import User, UserRole
from app.notifications import send_broadcast
from app.sanitization import sanitize_text
from app.schemas.admin import (
    BLOOD_GROUPS,
    AdminDonorRow,
    AdminHospitalRow,
    AdminOverview,
    BroadcastRequest,
    BroadcastResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])

# A mass SMS is expensive and hard to undo, so a second one right after the first is refused.
BROADCAST_COOLDOWN_SECONDS = 60
DONOR_LIST_LIMIT = 1000


def _require_admin(user: User) -> None:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")


def _donor_status(r: DonorProfile, today: date) -> str:
    if r.suspended:
        return "suspended"
    if not r.is_available:
        return "offline"
    if r.last_donation_date is not None and (today - r.last_donation_date).days < 56:
        return "locked"
    return "available"


def _get_donor_or_404(db: Session, donor_user_id: uuid.UUID) -> DonorProfile:
    row = db.query(DonorProfile).filter(DonorProfile.user_id == donor_user_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Donor not found")
    return row


def _get_hospital_or_404(db: Session, hospital_user_id: uuid.UUID) -> HospitalProfile:
    row = db.query(HospitalProfile).filter(HospitalProfile.user_id == hospital_user_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hospital not found")
    return row


@router.get("/overview", response_model=AdminOverview)
def overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AdminOverview:
    _require_admin(user)
    today = date.today()
    donors = db.query(DonorProfile).all()
    counts = {"available": 0, "locked": 0, "offline": 0, "suspended": 0}
    by_group = {g: 0 for g in sorted(BLOOD_GROUPS)}
    for r in donors:
        counts[_donor_status(r, today)] += 1
        if is_donor_eligible(r) and r.blood_group in by_group:
            by_group[r.blood_group] += 1
    active = (
        db.query(BloodRequest)
        .filter(BloodRequest.status.in_([RequestStatus.submitted, RequestStatus.matched, RequestStatus.alerted, RequestStatus.accepted]))
        .count()
    )
    start = datetime.combine(today, time.min).replace(tzinfo=timezone.utc)
    fulfilled_today = (
        db.query(BloodRequest)
        .filter(BloodRequest.status == RequestStatus.fulfilled, BloodRequest.fulfilled_at.isnot(None), BloodRequest.fulfilled_at >= start)
        .count()
    )
    hospitals = db.query(HospitalProfile).all()
    return AdminOverview(
        total_donors=len(donors),
        available_now=sum(by_group.values()),
        active_requests=active,
        fulfilled_today=fulfilled_today,
        locked=counts["locked"],
        offline=counts["offline"],
        suspended=counts["suspended"],
        hospitals_total=len(hospitals),
        hospitals_verified=sum(1 for h in hospitals if h.verified),
        available_by_group=by_group,
    )


@router.get("/donors", response_model=list[AdminDonorRow])
def list_donors(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[AdminDonorRow]:
    _require_admin(user)
    today = date.today()
    rows = db.query(DonorProfile).order_by(DonorProfile.updated_at.desc()).limit(DONOR_LIST_LIMIT).all()
    return [
        AdminDonorRow(
            user_id=r.user_id,
            full_name=r.full_name,
            blood_group=r.blood_group,
            area_text=r.area_text,
            status=_donor_status(r, today),
            last_donation_date=r.last_donation_date,
            verified=r.verified,
            suspended=r.suspended,
        )
        for r in rows
    ]


def _donor_flag(action: str, field: str, value: bool, donor_user_id: uuid.UUID, user: User, db: Session, label: str) -> dict:
    _require_admin(user)
    row = _get_donor_or_404(db, donor_user_id)
    setattr(row, field, value)
    db.commit()
    write_audit_log(db, actor_user_id=user.id, action=action, target_type="user", target_id=str(donor_user_id))
    return {"status": label}


@router.post("/donors/{donor_user_id}/suspend", response_model=dict)
def suspend_donor(donor_user_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return _donor_flag("admin.donor.suspend", "suspended", True, donor_user_id, user, db, "suspended")


@router.post("/donors/{donor_user_id}/unsuspend", response_model=dict)
def unsuspend_donor(donor_user_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return _donor_flag("admin.donor.unsuspend", "suspended", False, donor_user_id, user, db, "active")


@router.post("/donors/{donor_user_id}/verify", response_model=dict)
def verify_donor(donor_user_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return _donor_flag("admin.donor.verify", "verified", True, donor_user_id, user, db, "verified")


@router.post("/donors/{donor_user_id}/unverify", response_model=dict)
def unverify_donor(donor_user_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return _donor_flag("admin.donor.unverify", "verified", False, donor_user_id, user, db, "unverified")


@router.get("/hospitals", response_model=list[AdminHospitalRow])
def list_hospitals(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[AdminHospitalRow]:
    _require_admin(user)
    rows = (
        db.query(HospitalProfile, User)
        .join(User, User.id == HospitalProfile.user_id)
        .order_by(HospitalProfile.verified.asc(), HospitalProfile.created_at.desc())
        .limit(500)
        .all()
    )
    return [
        AdminHospitalRow(
            user_id=h.user_id,
            facility_name=h.facility_name,
            facility_type=h.facility_type,
            city=h.city,
            contact_person=h.contact_person,
            phone=u.phone,
            verified=h.verified,
            created_at=h.created_at,
        )
        for h, u in rows
    ]


def _hospital_flag(action: str, value: bool, hospital_user_id: uuid.UUID, user: User, db: Session) -> dict:
    _require_admin(user)
    row = _get_hospital_or_404(db, hospital_user_id)
    row.verified = value
    db.commit()
    write_audit_log(db, actor_user_id=user.id, action=action, target_type="user", target_id=str(hospital_user_id))
    return {"status": "verified" if value else "unverified"}


@router.post("/hospitals/{hospital_user_id}/verify", response_model=dict)
def verify_hospital(hospital_user_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return _hospital_flag("admin.hospital.verify", True, hospital_user_id, user, db)


@router.post("/hospitals/{hospital_user_id}/unverify", response_model=dict)
def unverify_hospital(hospital_user_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return _hospital_flag("admin.hospital.unverify", False, hospital_user_id, user, db)


@router.post("/broadcast", response_model=BroadcastResponse)
def broadcast(
    body: BroadcastRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BroadcastResponse:
    _require_admin(user)
    if body.audience == "blood_groups" and not body.blood_groups:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose at least one blood group")

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=BROADCAST_COOLDOWN_SECONDS)
    recent = (
        db.query(AuditLog)
        .filter(AuditLog.action == "admin.broadcast", AuditLog.actor_user_id == user.id, AuditLog.created_at >= cutoff)
        .first()
    )
    if recent is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"A broadcast was just sent. Wait {BROADCAST_COOLDOWN_SECONDS} seconds.")

    if body.audience == "all_hospitals":
        phones = [row[0] for row in db.query(User.phone).join(HospitalProfile, HospitalProfile.user_id == User.id).all()]
    else:
        wanted = set(body.blood_groups) if body.audience == "blood_groups" else None
        profiles = [p for p in db.query(DonorProfile).all() if is_donor_eligible(p) and (wanted is None or p.blood_group in wanted)]
        ids = [p.user_id for p in profiles]
        phones = [row[0] for row in db.query(User.phone).filter(User.id.in_(ids)).all()] if ids else []

    message = sanitize_text(body.message)
    delivery = "queued" if sms.is_configured() else "not_sent_sms_not_configured"
    background_tasks.add_task(send_broadcast, phones, message)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="admin.broadcast",
        target_type="broadcast",
        target_id=body.audience,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        meta={"message_len": len(message), "audience": body.audience, "groups": body.blood_groups, "recipients": len(phones), "delivery": delivery},
    )
    return BroadcastResponse(recipients=len(phones), audience=body.audience, delivery=delivery)
