"""Business-level SMS notifications. Meant to run as FastAPI background tasks,
so each function opens (and closes) its own database session."""
import logging
import uuid

from app import sms
from app.config import get_settings
from app.database import SessionLocal
from app.models.donor_profile import DonorProfile
from app.models.hospital_profile import HospitalProfile
from app.models.request import BloodRequest
from app.models.user import User

log = logging.getLogger(__name__)


def short_name(full_name: str | None) -> str:
    parts = (full_name or "").split()
    if not parts:
        return "Donor"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


def notify_donors_alerted(request_id: uuid.UUID, donor_user_ids: list[uuid.UUID]) -> None:
    if not donor_user_ids:
        return
    s = get_settings()
    db = SessionLocal()
    try:
        req = db.query(BloodRequest).filter(BloodRequest.id == request_id).first()
        if req is None:
            return
        hosp = db.query(HospitalProfile).filter(HospitalProfile.user_id == req.hospital_user_id).first()
        phones = [u.phone for u in db.query(User).filter(User.id.in_(donor_user_ids)).all()]
        variables = {
            "VAR1": req.blood_group,
            "VAR2": str(req.units),
            "VAR3": (hosp.facility_name if hosp else "a hospital")[:40],
            "VAR4": (hosp.city if hosp else req.location_text)[:30],
        }
        sent = sms.send_flow(s.msg91_donor_alert_template_id, [(p, variables) for p in phones])
        log.info("donor alert sms: request=%s alerted=%d sent=%d", request_id, len(phones), sent)
    except Exception:  # never let a notification failure bubble up
        log.exception("notify_donors_alerted failed")
    finally:
        db.close()


def notify_hospital_accepted(request_id: uuid.UUID, donor_user_id: uuid.UUID) -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        req = db.query(BloodRequest).filter(BloodRequest.id == request_id).first()
        donor = db.query(DonorProfile).filter(DonorProfile.user_id == donor_user_id).first()
        donor_user = db.query(User).filter(User.id == donor_user_id).first()
        hospital_user = db.query(User).filter(User.id == req.hospital_user_id).first() if req else None
        if not (req and donor and donor_user and hospital_user):
            return
        sms.send_flow(
            s.msg91_hospital_accept_template_id,
            [
                (
                    hospital_user.phone,
                    {
                        "VAR1": short_name(donor.full_name),
                        "VAR2": req.blood_group,
                        "VAR3": donor_user.phone,
                    },
                )
            ],
        )
    except Exception:
        log.exception("notify_hospital_accepted failed")
    finally:
        db.close()


BROADCAST_CHUNK = 200


def send_broadcast(phones: list[str], message: str) -> None:
    """Send an admin broadcast in chunks. Runs as a background task; never raises."""
    s = get_settings()
    sent = 0
    try:
        for i in range(0, len(phones), BROADCAST_CHUNK):
            chunk = phones[i : i + BROADCAST_CHUNK]
            sent += sms.send_flow(s.msg91_broadcast_template_id, [(p, {"VAR1": message}) for p in chunk])
        log.info("broadcast sms: recipients=%d sent=%d", len(phones), sent)
    except Exception:
        log.exception("send_broadcast failed")
