import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.audit import write_audit_log
from app.guards import ensure_hospital_may_request
from app.notifications import notify_donors_alerted
from app.database import SessionLocal, get_db
from app.deps import get_current_user
from app.matching import (
    COMPATIBILITY_MATRIX,
    distance_km,
    extract_lat_lng,
    is_donor_eligible,
)
from app.models.donor_profile import DonorProfile
from app.models.request import BloodRequest, RequestStatus
from app.models.request_alert import AlertStatus, RequestAlert
from app.models.user import User, UserRole
from app.schemas.emergency import EmergencyTriggerRequest, EmergencyTriggerResponse

router = APIRouter(prefix="/emergency", tags=["emergency"])

log = logging.getLogger(__name__)

RADII = [5, 10, 20, 40, 50]
WAIT_SECONDS = 180


@router.post("/trigger", response_model=EmergencyTriggerResponse)
def trigger_emergency(
    body: EmergencyTriggerRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmergencyTriggerResponse:
    if user.role != UserRole.hospital:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hospital role required")
    ensure_hospital_may_request(db, user)

    req = db.query(BloodRequest).filter(BloodRequest.id == body.request_id).first()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    if req.hospital_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your request")
    if not body.is_emergency:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="is_emergency must be true")
    if body.blood_group != req.blood_group:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blood_group mismatch for request")

    write_audit_log(
        db,
        actor_user_id=user.id,
        action="emergency.trigger",
        target_type="request",
        target_id=str(req.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        meta={"latitude": body.latitude, "longitude": body.longitude, "radii": RADII},
    )

    background_tasks.add_task(
        run_emergency_escalation,
        request_id=req.id,
        actor_user_id=user.id,
        latitude=body.latitude,
        longitude=body.longitude,
        blood_group=body.blood_group,
    )
    return EmergencyTriggerResponse(started=True, radii=RADII)


async def run_emergency_escalation(
    *,
    request_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    latitude: float,
    longitude: float,
    blood_group: str,
) -> None:
    """Widen the search radius step by step.

    Async on purpose: the waiting between steps is an `asyncio.sleep`, so it does not tie up a
    worker thread or a database session for minutes (many simultaneous emergencies used to be
    able to exhaust the thread pool and freeze every other endpoint).
    """
    for index, radius in enumerate(RADII):
        should_stop = await run_in_threadpool(
            _escalation_step,
            request_id=request_id,
            actor_user_id=actor_user_id,
            latitude=latitude,
            longitude=longitude,
            blood_group=blood_group,
            radius=radius,
            first=index == 0,
        )
        if should_stop:
            return
        if radius != RADII[-1]:
            await asyncio.sleep(WAIT_SECONDS)
    await run_in_threadpool(_escalation_finish, request_id=request_id, actor_user_id=actor_user_id)


def _escalation_step(
    *,
    request_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    latitude: float,
    longitude: float,
    blood_group: str,
    radius: int,
    first: bool,
) -> bool:
    """Alert donors inside one radius. Returns True when the escalation should stop."""
    db = SessionLocal()
    try:
        req = db.query(BloodRequest).filter(BloodRequest.id == request_id).first()
        if req is None:
            return True
        if first and req.status in {RequestStatus.cancelled, RequestStatus.fulfilled}:
            write_audit_log(
                db,
                actor_user_id=actor_user_id,
                action="emergency.abort",
                target_type="request",
                target_id=str(request_id),
                meta={"reason": f"request_status_{req.status.value}"},
            )
            return True
        if req.status in {RequestStatus.accepted, RequestStatus.cancelled, RequestStatus.fulfilled}:
            write_audit_log(
                db,
                actor_user_id=actor_user_id,
                action="emergency.stop",
                target_type="request",
                target_id=str(request_id),
                meta={"reason": f"request_status_{req.status.value}", "radius": radius},
            )
            return True

        candidates, unknown_distance_count = _find_eligible_donors(
            db=db, blood_group=blood_group, center=(latitude, longitude), radius_km=radius
        )
        if unknown_distance_count:
            log.info("emergency.distance_unknown request_id=%s radius_km=%s donors=%s", request_id, radius, unknown_distance_count)
        new_ids = _create_alerts_for_radius(db=db, req=req, donors=candidates)
        notify_donors_alerted(req.id, new_ids)
        if new_ids and req.status != RequestStatus.alerted:
            req.status = RequestStatus.alerted
            req.alerted_at = datetime.now(timezone.utc)
            db.commit()
        write_audit_log(
            db,
            actor_user_id=actor_user_id,
            action="emergency.radius.alerted",
            target_type="request",
            target_id=str(request_id),
            meta={"radius": radius, "count": len(new_ids), "unknown_distance_count": unknown_distance_count},
        )
        return False
    finally:
        db.close()


def _escalation_finish(*, request_id: uuid.UUID, actor_user_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        req = db.query(BloodRequest).filter(BloodRequest.id == request_id).first()
        if req is not None and req.status in {RequestStatus.accepted, RequestStatus.cancelled, RequestStatus.fulfilled}:
            write_audit_log(
                db,
                actor_user_id=actor_user_id,
                action="emergency.stop",
                target_type="request",
                target_id=str(request_id),
                meta={"reason": f"request_status_{req.status.value}", "after_radii": True},
            )
            return
        write_audit_log(
            db,
            actor_user_id=actor_user_id,
            action="emergency.completed",
            target_type="request",
            target_id=str(request_id),
            meta={"result": "radii_exhausted"},
        )
    finally:
        db.close()


def _find_eligible_donors(
    *,
    db: Session,
    blood_group: str,
    center: tuple[float, float],
    radius_km: int,
) -> tuple[list[DonorProfile], int]:
    compatible_bloods = set(COMPATIBILITY_MATRIX.get(blood_group, {}).keys())
    rows = db.query(DonorProfile).filter(DonorProfile.blood_group.in_(compatible_bloods)).all()
    eligible = [p for p in rows if is_donor_eligible(p)]
    in_radius: list[DonorProfile] = []
    unknown_distance: list[DonorProfile] = []
    for profile in eligible:
        if profile.latitude is not None and profile.longitude is not None:
            donor_geo = (profile.latitude, profile.longitude)
        else:
            donor_geo = extract_lat_lng(profile.area_text)
        if donor_geo is None:
            unknown_distance.append(profile)
            continue
        if distance_km(center, donor_geo) <= radius_km:
            in_radius.append(profile)
    return (in_radius + unknown_distance, len(unknown_distance))


def _create_alerts_for_radius(
    *,
    db: Session,
    req: BloodRequest,
    donors: list[DonorProfile],
) -> list[uuid.UUID]:
    existing_ids = {
        row[0]
        for row in db.query(RequestAlert.donor_user_id).filter(RequestAlert.request_id == req.id).all()
    }
    created: list[uuid.UUID] = []
    for donor in donors:
        if donor.user_id in existing_ids:
            continue
        db.add(
            RequestAlert(
                request_id=req.id,
                donor_user_id=donor.user_id,
                status=AlertStatus.alerted,
            )
        )
        existing_ids.add(donor.user_id)
        created.append(donor.user_id)
    if created:
        db.commit()
    return created
