from datetime import date, datetime, timezone
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.audit import write_audit_log
from app.database import get_db
from app.deps import get_current_user
from app.matching import COMPATIBILITY_MATRIX, is_donor_eligible
from app.models.donor_profile import DonorProfile
from app.models.hospital_profile import HospitalProfile
from app.models.request import BloodRequest, RequestStatus
from app.models.request_alert import AlertStatus, RequestAlert
from app.models.request_match import MatchStatus, RequestMatch
from app.models.user import User, UserRole
from app.schemas.request import (
    AlertActionResponse,
    MatchPublic,
    RequestCreate,
    RequestCreateResponse,
    RequestPublic,
)
from app.guards import ensure_hospital_may_request
from app.notifications import notify_donors_alerted, notify_hospital_accepted, short_name
from app.utils.geofence import best_distance_km

router = APIRouter(prefix="/requests", tags=["requests"])
FIRST_WAVE_SIZE = 5
settings = get_settings()


def _require_hospital(user: User) -> None:
    if user.role != UserRole.hospital:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hospital role required",
        )


def _require_donor(user: User) -> None:
    if user.role != UserRole.donor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Donor role required",
        )


def _get_request_or_404(db: Session, request_id: uuid.UUID) -> BloodRequest:
    req = (
        db.query(BloodRequest)
        .options(
            selectinload(BloodRequest.alerts),
            selectinload(BloodRequest.matches),
        )
        .filter(BloodRequest.id == request_id)
        .first()
    )
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found",
        )
    return req


@router.post(
    "",
    response_model=RequestCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_request(
    body: RequestCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestCreateResponse:
    _require_hospital(user)
    ensure_hospital_may_request(db, user)
    geofence_km = body.geofence_km or settings.default_geofence_km
    my_hosp = db.query(HospitalProfile).filter(HospitalProfile.user_id == user.id).first()
    center: tuple[float, float] | None = None
    if body.latitude is not None and body.longitude is not None:
        center = (body.latitude, body.longitude)
    elif my_hosp is not None and my_hosp.latitude is not None and my_hosp.longitude is not None:
        center = (my_hosp.latitude, my_hosp.longitude)

    now = datetime.now(timezone.utc)
    req = BloodRequest(
        hospital_user_id=user.id,
        blood_group=body.blood_group,
        units=body.units,
        urgency=body.urgency,
        location_text=body.location_text.strip(),
        notes=body.notes.strip() if body.notes else None,
        latitude=center[0] if center else None,
        longitude=center[1] if center else None,
        status=RequestStatus.submitted,
    )
    db.add(req)
    db.flush()

    candidate_bloods = set(
        COMPATIBILITY_MATRIX.get(body.blood_group, {}).keys()
    )
    candidates = db.query(DonorProfile).filter(
        DonorProfile.blood_group.in_(candidate_bloods)
    ).all()
    dist_by_user: dict[uuid.UUID, float | None] = {}
    eligible: list[DonorProfile] = []
    for p in candidates:
        if not is_donor_eligible(p):
            continue
        d = best_distance_km(center, body.location_text, p.latitude, p.longitude, p.area_text)
        if d is not None and d > geofence_km:
            continue  # known to be outside the radius; unknown distance stays eligible
        dist_by_user[p.user_id] = d
        eligible.append(p)

    ranked_rows: list[RequestMatch] = []
    if eligible:
        scored: list[tuple[DonorProfile, float, float, float | None]] = []
        for profile in eligible:
            comp = COMPATIBILITY_MATRIX.get(body.blood_group, {}).get(
                profile.blood_group, 0.0
            )
            if comp <= 0:
                continue
            dist = dist_by_user.get(profile.user_id)
            distance_penalty = (dist * 2.0) if dist is not None else 20.0
            final = (comp * 100.0) - distance_penalty
            scored.append((profile, comp * 100.0, final, dist))

        scored.sort(
            key=lambda row: (
                -row[2],
                row[3] if row[3] is not None else float("inf"),
            )
        )

        req.status = RequestStatus.matched
        req.matched_at = now
        for idx, (profile, comp_score, final_score, dist) in enumerate(
            scored, start=1
        ):
            match = RequestMatch(
                request_id=req.id,
                donor_user_id=profile.user_id,
                rank=idx,
                compatibility_score=round(comp_score, 2),
                distance_km=round(dist, 2) if dist is not None else None,
                final_score=round(final_score, 2),
                status=MatchStatus.queued,
            )
            db.add(match)
            ranked_rows.append(match)

        for match in ranked_rows[:FIRST_WAVE_SIZE]:
            db.add(
                RequestAlert(
                    request_id=req.id,
                    donor_user_id=match.donor_user_id,
                    status=AlertStatus.alerted,
                )
            )
            match.status = MatchStatus.alerted
        req.status = RequestStatus.alerted
        req.alerted_at = now

    db.commit()
    req = _get_request_or_404(db, req.id)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="request.create",
        target_type="request",
        target_id=str(req.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        meta={
            "blood_group": req.blood_group,
            "urgency": req.urgency.value,
            "units": req.units,
        },
    )
    first_wave = ranked_rows[:FIRST_WAVE_SIZE]
    background_tasks.add_task(notify_donors_alerted, req.id, [m.donor_user_id for m in first_wave])
    return RequestCreateResponse(
        request=RequestPublic.model_validate(req),
        first_wave=[MatchPublic.model_validate(m) for m in first_wave],
    )


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _feed_item(req: BloodRequest, hosp: HospitalProfile | None, my_alert_status: str | None = None) -> dict:
    """Lightweight request row for dashboards (no donor ids / match internals)."""
    return {
        "id": str(req.id),
        "hospital_user_id": str(req.hospital_user_id),
        "hospital_facility_name": hosp.facility_name if hosp else None,
        "hospital_city": hosp.city if hosp else None,
        "blood_group": req.blood_group,
        "units": req.units,
        "urgency": req.urgency.value,
        "location_text": req.location_text,
        "status": req.status.value,
        "accepted_donor_user_id": str(req.accepted_donor_user_id) if req.accepted_donor_user_id else None,
        "created_at": _iso(req.created_at),
        "matched_at": _iso(req.matched_at),
        "alerted_at": _iso(req.alerted_at),
        "accepted_at": _iso(req.accepted_at),
        "fulfilled_at": _iso(req.fulfilled_at),
        "cancelled_at": _iso(req.cancelled_at),
        "my_alert_status": my_alert_status,
    }


@router.get("/inbox")
def donor_inbox(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Requests this donor was alerted about (newest first). Declined ones are hidden."""
    _require_donor(user)
    rows = (
        db.query(RequestAlert, BloodRequest, HospitalProfile)
        .join(BloodRequest, BloodRequest.id == RequestAlert.request_id)
        .outerjoin(HospitalProfile, HospitalProfile.user_id == BloodRequest.hospital_user_id)
        .filter(RequestAlert.donor_user_id == user.id)
        .filter(RequestAlert.status != AlertStatus.declined)
        .order_by(BloodRequest.created_at.desc())
        .limit(100)
        .all()
    )
    return [_feed_item(req, hosp, alert.status.value) for alert, req, hosp in rows]


@router.get("/mine")
def hospital_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """This hospital's own requests (newest first)."""
    _require_hospital(user)
    hosp = db.query(HospitalProfile).filter(HospitalProfile.user_id == user.id).first()
    reqs = (
        db.query(BloodRequest)
        .options(selectinload(BloodRequest.alerts), selectinload(BloodRequest.matches))
        .filter(BloodRequest.hospital_user_id == user.id)
        .order_by(BloodRequest.created_at.desc())
        .limit(100)
        .all()
    )
    donor_ids = {m.donor_user_id for r in reqs for m in r.matches}
    profiles = {
        p.user_id: p for p in db.query(DonorProfile).filter(DonorProfile.user_id.in_(donor_ids))
    } if donor_ids else {}
    out = []
    for r in reqs:
        item = _feed_item(r, hosp)
        alert_by_donor = {a.donor_user_id: a for a in r.alerts}
        donors = []
        for m in sorted(r.matches, key=lambda m: m.rank)[:10]:
            p = profiles.get(m.donor_user_id)
            if p is None:
                continue
            a = alert_by_donor.get(m.donor_user_id)
            donors.append(
                {
                    "label": short_name(p.full_name),
                    "blood_group": p.blood_group,
                    "distance_km": m.distance_km,
                    "status": a.status.value if a else m.status.value,
                }
            )
        item["donors"] = donors
        item["alerted_pending"] = sum(1 for a in r.alerts if a.status == AlertStatus.alerted)
        item["accepted_donor"] = None
        if r.status == RequestStatus.accepted and r.accepted_donor_user_id:
            p = profiles.get(r.accepted_donor_user_id)
            du = db.query(User).filter(User.id == r.accepted_donor_user_id).first()
            if p and du:
                # The donor opted in to sharing (consent_share) and confirmed this request, so the
                # hospital that asked may call them.
                item["accepted_donor"] = {"label": short_name(p.full_name), "blood_group": p.blood_group, "phone": du.phone}
        out.append(item)
    return out


@router.get("/city")
def city_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """City-wide live request feed for the admin dashboard."""
    if user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    rows = (
        db.query(BloodRequest, HospitalProfile)
        .outerjoin(HospitalProfile, HospitalProfile.user_id == BloodRequest.hospital_user_id)
        .order_by(BloodRequest.created_at.desc())
        .limit(100)
        .all()
    )
    return [_feed_item(r, h) for r, h in rows]


@router.get("/{request_id}", response_model=RequestPublic)
def view_request(
    request_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestPublic:
    req = _get_request_or_404(db, request_id)
    if user.role == UserRole.hospital and req.hospital_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your request",
        )
    if user.role == UserRole.donor:
        has_alert = any(a.donor_user_id == user.id for a in req.alerts)
        if not has_alert:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to this request",
            )
    return RequestPublic.model_validate(req)


@router.post("/{request_id}/cancel", response_model=RequestPublic)
def cancel_request(
    request_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestPublic:
    _require_hospital(user)
    req = _get_request_or_404(db, request_id)
    if req.hospital_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your request",
        )
    if req.status not in {
        RequestStatus.submitted,
        RequestStatus.matched,
        RequestStatus.alerted,
        RequestStatus.accepted,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel request in state {req.status.value}",
        )
    req.status = RequestStatus.cancelled
    req.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="request.cancel",
        target_type="request",
        target_id=str(req.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return RequestPublic.model_validate(req)


@router.post("/{request_id}/fulfill", response_model=RequestPublic)
def fulfill_request(
    request_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestPublic:
    _require_hospital(user)
    req = _get_request_or_404(db, request_id)
    if req.hospital_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your request",
        )
    if req.status != RequestStatus.accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only accepted requests can be fulfilled",
        )
    req.status = RequestStatus.fulfilled
    req.fulfilled_at = datetime.now(timezone.utc)
    if req.accepted_donor_user_id:
        donor_profile = (
            db.query(DonorProfile).filter(DonorProfile.user_id == req.accepted_donor_user_id).first()
        )
        if donor_profile is not None:
            donor_profile.last_donation_date = date.today()  # starts the 56-day lock
    db.commit()
    db.refresh(req)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="request.fulfill",
        target_type="request",
        target_id=str(req.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return RequestPublic.model_validate(req)


@router.post("/{request_id}/accept", response_model=AlertActionResponse)
def accept_alert(
    request_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AlertActionResponse:
    _require_donor(user)
    req = _get_request_or_404(db, request_id)
    if req.status != RequestStatus.alerted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is not accepting donor responses",
        )
    alert = (
        db.query(RequestAlert)
        .filter(
            RequestAlert.request_id == req.id,
            RequestAlert.donor_user_id == user.id,
        )
        .first()
    )
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No alert assigned to this donor",
        )
    if alert.status != AlertStatus.alerted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Alert already {alert.status.value}",
        )
    me_profile = db.query(DonorProfile).filter(DonorProfile.user_id == user.id).first()
    if me_profile is not None and me_profile.suspended:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account is suspended")

    now = datetime.now(timezone.utc)
    alert.status = AlertStatus.accepted
    alert.responded_at = now
    selected_match = (
        db.query(RequestMatch)
        .filter(
            RequestMatch.request_id == req.id,
            RequestMatch.donor_user_id == user.id,
        )
        .first()
    )
    if selected_match:
        selected_match.status = MatchStatus.accepted

    req.status = RequestStatus.accepted
    req.accepted_donor_user_id = user.id
    req.accepted_at = now

    others = (
        db.query(RequestAlert)
        .filter(
            RequestAlert.request_id == req.id,
            RequestAlert.donor_user_id != user.id,
            RequestAlert.status == AlertStatus.alerted,
        )
        .all()
    )
    for row in others:
        row.status = AlertStatus.declined
        row.responded_at = now
    db.query(RequestMatch).filter(
        RequestMatch.request_id == req.id,
        RequestMatch.donor_user_id != user.id,
        RequestMatch.status == MatchStatus.alerted,
    ).update({"status": MatchStatus.declined}, synchronize_session=False)

    db.commit()
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="request.alert.accept",
        target_type="request",
        target_id=str(req.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    background_tasks.add_task(notify_hospital_accepted, req.id, user.id)
    return AlertActionResponse(status="accepted", request_status=req.status)


def _escalate_next_wave(db: Session, req: BloodRequest) -> list[uuid.UUID]:
    """When every currently-alerted donor has declined, alert the next queued donors."""
    still_waiting = (
        db.query(RequestAlert)
        .filter(RequestAlert.request_id == req.id, RequestAlert.status == AlertStatus.alerted)
        .count()
    )
    if still_waiting:
        return []
    queued = (
        db.query(RequestMatch)
        .filter(RequestMatch.request_id == req.id, RequestMatch.status == MatchStatus.queued)
        .order_by(RequestMatch.rank)
        .limit(FIRST_WAVE_SIZE)
        .all()
    )
    for m in queued:
        db.add(RequestAlert(request_id=req.id, donor_user_id=m.donor_user_id, status=AlertStatus.alerted))
        m.status = MatchStatus.alerted
    return [m.donor_user_id for m in queued]


@router.post("/{request_id}/decline", response_model=AlertActionResponse)
def decline_alert(
    request_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AlertActionResponse:
    _require_donor(user)
    req = _get_request_or_404(db, request_id)
    if req.status != RequestStatus.alerted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request is not accepting donor responses",
        )
    alert = (
        db.query(RequestAlert)
        .filter(
            RequestAlert.request_id == req.id,
            RequestAlert.donor_user_id == user.id,
        )
        .first()
    )
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No alert assigned to this donor",
        )
    if alert.status != AlertStatus.alerted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Alert already {alert.status.value}",
        )
    alert.status = AlertStatus.declined
    alert.responded_at = datetime.now(timezone.utc)
    row = (
        db.query(RequestMatch)
        .filter(
            RequestMatch.request_id == req.id,
            RequestMatch.donor_user_id == user.id,
        )
        .first()
    )
    if row and row.status in {MatchStatus.queued, MatchStatus.alerted}:
        row.status = MatchStatus.declined
    db.flush()
    next_wave = _escalate_next_wave(db, req)
    db.commit()
    if next_wave:
        background_tasks.add_task(notify_donors_alerted, req.id, next_wave)
    write_audit_log(
        db,
        actor_user_id=user.id,
        action="request.alert.decline",
        target_type="request",
        target_id=str(req.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return AlertActionResponse(status="declined", request_status=req.status)
