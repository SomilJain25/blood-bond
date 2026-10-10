import math
import re


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
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def best_distance_km(
    center: tuple[float, float] | None,
    center_text: str | None,
    lat: float | None,
    lng: float | None,
    text: str | None,
) -> float | None:
    """Distance using stored coordinates when present, then coordinates found in text,
    then an exact text match (0 km). None when it cannot be determined."""
    a = center or extract_lat_lng(center_text or "")
    b = (lat, lng) if lat is not None and lng is not None else extract_lat_lng(text or "")
    if a and b:
        return distance_km(a, b)
    if center_text and text and center_text.strip().lower() == text.strip().lower():
        return 0.0
    return None


def estimate_distance_km(location_a: str, location_b: str) -> float | None:
    geo_a = extract_lat_lng(location_a)
    geo_b = extract_lat_lng(location_b)
    if geo_a and geo_b:
        return distance_km(geo_a, geo_b)
    if location_a.strip().lower() == location_b.strip().lower():
        return 0.0
    return None


def is_within_geofence(
    center_location: str, target_location: str, radius_km: float
) -> bool:
    dist = estimate_distance_km(center_location, target_location)
    if dist is None:
        # If we cannot compute coordinates, keep donor eligible.
        return True
    return dist <= radius_km
