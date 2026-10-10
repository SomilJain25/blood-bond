from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.matching import is_donor_eligible
from app.models.donor_profile import DonorProfile
from app.models.hospital_profile import HospitalProfile
from app.models.request import BloodRequest, RequestStatus
from app.models.user import User, UserRole
from app.notifications import short_name
from app.utils.geofence import distance_km, extract_lat_lng

router = APIRouter(prefix="/map", tags=["map"])

ACTIVE = (RequestStatus.submitted, RequestStatus.matched, RequestStatus.alerted, RequestStatus.accepted)
# Donor locations are rounded to 2 decimals (~1.1 km): enough for a radius view, but not a home address.
DONOR_COORD_DECIMALS = 2


@router.get("/data")
def map_data(
    blood_group: str | None = Query(None, max_length=5),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Everything the map page needs. Hospitals and admins only."""
    if user.role not in (UserRole.hospital, UserRole.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hospital or admin role required")

    me_hosp = None
    center = None
    if user.role == UserRole.hospital:
        me_hosp = db.query(HospitalProfile).filter(HospitalProfile.user_id == user.id).first()
        if me_hosp and me_hosp.latitude is not None and me_hosp.longitude is not None:
            center = (me_hosp.latitude, me_hosp.longitude)
        elif me_hosp and me_hosp.gps_text:
            center = extract_lat_lng(me_hosp.gps_text)

    dq = (
        db.query(DonorProfile)
        .join(User, User.id == DonorProfile.user_id)
        .filter(DonorProfile.consent_share.is_(True))
        .filter(User.phone_verified.is_(True))
        .filter(DonorProfile.latitude.isnot(None), DonorProfile.longitude.isnot(None))
    )
    if blood_group:
        dq = dq.filter(DonorProfile.blood_group == blood_group.strip().upper().replace("−", "-"))

    donors = []
    for p in dq.all():
        lat = round(p.latitude, DONOR_COORD_DECIMALS)
        lng = round(p.longitude, DONOR_COORD_DECIMALS)
        dist = round(distance_km(center, (lat, lng)), 1) if center else None
        area = p.area_text if extract_lat_lng(p.area_text or "") is None else None
        donors.append(
            {
                "label": short_name(p.full_name),
                "blood_group": p.blood_group,
                "area": area,
                "lat": lat,
                "lng": lng,
                "available": is_donor_eligible(p),
                "verified": p.verified,
                "distance_km": dist,
            }
        )
    donors.sort(key=lambda d: (d["distance_km"] is None, d["distance_km"] or 0))

    hospitals = [
        {
            "id": str(h.id),
            "name": h.facility_name,
            "city": h.city,
            "type": h.facility_type,
            "verified": h.verified,
            "lat": h.latitude,
            "lng": h.longitude,
            "is_me": bool(me_hosp and h.id == me_hosp.id),
        }
        for h in db.query(HospitalProfile)
        .filter(HospitalProfile.latitude.isnot(None), HospitalProfile.longitude.isnot(None))
        .all()
    ]

    rq = (
        db.query(BloodRequest, HospitalProfile)
        .outerjoin(HospitalProfile, HospitalProfile.user_id == BloodRequest.hospital_user_id)
        .filter(BloodRequest.status.in_(ACTIVE))
        .order_by(BloodRequest.created_at.desc())
    )
    if user.role == UserRole.hospital:
        rq = rq.filter(BloodRequest.hospital_user_id == user.id)
    requests = []
    for r, h in rq.limit(50).all():
        lat, lng = r.latitude, r.longitude
        if lat is None and h is not None:
            lat, lng = h.latitude, h.longitude
        requests.append(
            {
                "id": str(r.id),
                "blood_group": r.blood_group,
                "units": r.units,
                "urgency": r.urgency.value,
                "status": r.status.value,
                "location_text": r.location_text,
                "hospital_name": h.facility_name if h else None,
                "lat": lat,
                "lng": lng,
            }
        )

    return {
        "center": {"lat": center[0], "lng": center[1]} if center else None,
        "donors": donors,
        "hospitals": hospitals,
        "requests": requests,
        "summary": {
            "donors_total": len(donors),
            "donors_available": sum(1 for d in donors if d["available"]),
            "donors_within_10km": sum(1 for d in donors if d["distance_km"] is not None and d["distance_km"] <= 10),
        },
    }
