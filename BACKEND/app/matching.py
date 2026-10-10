from datetime import date
import math
import re

from app.models.donor_profile import DonorProfile

COMPATIBILITY_MATRIX: dict[str, dict[str, float]] = {
    "O-": {"O-": 1.0},
    "O+": {"O+": 1.0, "O-": 0.95},
    "A-": {"A-": 1.0, "O-": 0.95},
    "A+": {"A+": 1.0, "A-": 0.95, "O+": 0.9, "O-": 0.85},
    "B-": {"B-": 1.0, "O-": 0.95},
    "B+": {"B+": 1.0, "B-": 0.95, "O+": 0.9, "O-": 0.85},
    "AB-": {"AB-": 1.0, "A-": 0.92, "B-": 0.92, "O-": 0.88},
    "AB+": {
        "AB+": 1.0,
        "AB-": 0.96,
        "A+": 0.94,
        "A-": 0.92,
        "B+": 0.94,
        "B-": 0.92,
        "O+": 0.9,
        "O-": 0.88,
    },
}


def is_donor_eligible(profile: DonorProfile) -> bool:
    if not profile.consent_share:
        return False
    if profile.suspended or not profile.is_available:
        return False
    if profile.last_donation_date is None:
        return True
    return (date.today() - profile.last_donation_date).days >= 56


def extract_lat_lng(text: str) -> tuple[float, float] | None:
    m = re.search(r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", text)
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2))
    except ValueError:
        return None


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (
        (math.sin(dlat / 2) ** 2)
        + math.cos(lat1) * math.cos(lat2) * (math.sin(dlon / 2) ** 2)
    )
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def estimate_distance_km(req_location: str, donor_area: str) -> float | None:
    req_geo = extract_lat_lng(req_location)
    donor_geo = extract_lat_lng(donor_area)
    if req_geo and donor_geo:
        return distance_km(req_geo, donor_geo)
    if donor_area.strip().lower() == req_location.strip().lower():
        return 0.0
    return None
