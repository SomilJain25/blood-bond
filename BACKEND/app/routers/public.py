import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.matching import is_donor_eligible
from app.models.donor_profile import DonorProfile
from app.models.hospital_profile import HospitalProfile
from app.models.request import BloodRequest, RequestStatus

router = APIRouter(prefix="/public", tags=["public"])

_CACHE_SECONDS = 30
_cache: tuple[float, dict] | None = None


@router.get("/stats")
def public_stats(db: Session = Depends(get_db)) -> dict:
    """Aggregate counts for the landing page. No personal data. Cached for 30 s so public
    traffic cannot hammer the database."""
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _CACHE_SECONDS:
        return _cache[1]

    donors = db.query(DonorProfile).all()
    accepted = (
        db.query(BloodRequest)
        .filter(BloodRequest.accepted_at.isnot(None))
        .order_by(BloodRequest.created_at.desc())
        .limit(500)
        .all()
    )
    seconds = [(r.accepted_at - r.created_at).total_seconds() for r in accepted if r.created_at]
    payload = {
        "donors_total": len(donors),
        "donors_available": sum(1 for d in donors if is_donor_eligible(d)),
        "hospitals_total": db.query(HospitalProfile).count(),
        "requests_fulfilled": db.query(BloodRequest).filter(BloodRequest.status == RequestStatus.fulfilled).count(),
        "avg_match_seconds": round(sum(seconds) / len(seconds)) if seconds else None,
    }
    _cache = (now, payload)
    return payload
