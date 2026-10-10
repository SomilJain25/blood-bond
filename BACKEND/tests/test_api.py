"""End-to-end API tests. Run from BACKEND/:  pip install pytest && pytest -q"""
import os
import sqlite3
import tempfile
from datetime import date, timedelta

import pytest

_tmp = tempfile.mkdtemp()
_DB = os.path.join(_tmp, "t.db")

# An "old" database created before latitude/longitude existed -> startup must migrate it.
_con = sqlite3.connect(_DB)
_con.executescript(
    """
    CREATE TABLE donor_profiles (id CHAR(32) PRIMARY KEY, user_id CHAR(32) NOT NULL UNIQUE, full_name VARCHAR(200) NOT NULL,
      age INTEGER NOT NULL, weight_kg INTEGER NOT NULL, area_text VARCHAR(300) NOT NULL, blood_group VARCHAR(5) NOT NULL,
      last_donation_date DATE, medical_notes TEXT, aadhaar_last4 VARCHAR(4) NOT NULL, emergency_phone VARCHAR(15) NOT NULL,
      consent_share BOOLEAN NOT NULL, is_available BOOLEAN NOT NULL, created_at DATETIME, updated_at DATETIME);
    """
)
_con.commit()
_con.close()

os.environ.update(
    DATABASE_URL=f"sqlite:///{_DB}", DEMO_MODE="true", SECRET_KEY="test-secret", ADMIN_PHONES="8888888888", SMS_PROVIDER="none"
)

from fastapi.testclient import TestClient  # noqa: E402

from app import security, sms  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

GWALIOR = (26.2124, 78.1772)
CHENNAI = (13.0827, 80.2707)


@pytest.fixture(scope="module")
def client():
    security.OTP_RESEND_COOLDOWN_SECONDS = 0
    security.OTP_MAX_SENDS_PER_IP_WINDOW = 10_000
    security.OTP_MAX_SENDS_PER_PHONE_WINDOW = 10_000
    with TestClient(app) as c:
        yield c


def login(c, phone, role):
    code = c.post("/v1/auth/otp/send", json={"phone": phone}).json()["demo_otp"]
    r = c.post("/v1/auth/otp/verify", json={"phone": phone, "code": code, "role": role})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def donor_payload(name, bg, lat, lng, **extra):
    return {
        "full_name": name, "age": 30, "weight_kg": 70, "area_text": "Test Area", "blood_group": bg,
        "aadhaar_last4": "1234", "emergency_phone": "9876543210", "consent_share": True,
        "latitude": lat, "longitude": lng, **extra,
    }


def test_old_database_was_migrated(client):
    con = sqlite3.connect(_DB)
    cols = {r[1] for r in con.execute("PRAGMA table_info(donor_profiles)")}
    con.close()
    assert {"latitude", "longitude"} <= cols


def test_security_basics(client):
    code = client.post("/v1/auth/otp/send", json={"phone": "9000000001"}).json()["demo_otp"]
    r = client.post("/v1/auth/otp/verify", json={"phone": "9000000001", "code": code, "role": "admin"})
    assert r.status_code == 403  # not on the admin allow-list
    assert client.get("/v1/donors").status_code == 401  # directory is no longer anonymous
    login(client, "8888888888", "admin")  # allow-listed phone works


def test_otp_not_leaked_outside_demo_mode(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "demo_mode", False)
    monkeypatch.setattr(s, "sms_provider", "none")
    r = client.post("/v1/auth/otp/send", json={"phone": "9000000002"})
    assert r.status_code == 503 and "demo_otp" not in r.text  # refuses instead of leaking


def test_msg91_otp_request_shape_and_error_handling(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "sms_provider", "msg91")
    monkeypatch.setattr(s, "msg91_authkey", "KEY")
    monkeypatch.setattr(s, "msg91_otp_template_id", "OTPTPL")
    calls = []
    monkeypatch.setattr(sms, "_post", lambda url, params=None, json=None: calls.append((url, params, json)) or {"type": "success"})
    assert client.post("/v1/auth/otp/send", json={"phone": "9000000003"}).status_code == 200
    url, params, _ = calls[0]
    assert url.endswith("/api/v5/otp")
    assert params["mobile"] == "919000000003" and params["template_id"] == "OTPTPL" and len(params["otp"]) == 6
    # MSG91 answers HTTP 200 + {"type": "error"} on auth problems -> must surface as failure
    monkeypatch.setattr(sms, "_post", lambda *a, **k: {"type": "error", "message": "Invalid authkey"})
    assert client.post("/v1/auth/otp/send", json={"phone": "9000000004"}).status_code == 502


def test_full_request_lifecycle_with_coordinates_sms_and_escalation(client, monkeypatch):
    hosp = login(client, "6000000001", "hospital")
    assert client.post("/v1/profiles/hospital", headers=hosp, json={
        "facility_name": "City Hospital", "contact_person": "Dr A", "address": "MG Road", "city": "Gwalior",
        "consent": True, "latitude": GWALIOR[0], "longitude": GWALIOR[1]}).status_code == 200

    # 7 nearby O+ donors, 1 far-away O+ donor, 1 nearby donor of an incompatible group
    donors = {}
    for i in range(7):
        h = login(client, f"70000000{i:02d}", "donor")
        client.post("/v1/profiles/donor", headers=h, json=donor_payload(f"Near Donor{i}", "O+", GWALIOR[0] + 0.005 * i, GWALIOR[1]))
        donors[i] = h
    far = login(client, "7000000099", "donor")
    client.post("/v1/profiles/donor", headers=far, json=donor_payload("Far Away", "O+", 28.61, 77.20))  # Delhi
    wrong = login(client, "7000000098", "donor")
    client.post("/v1/profiles/donor", headers=wrong, json=donor_payload("Wrong Group", "B-", *GWALIOR))
    recent = login(client, "7000000097", "donor")
    assert client.post("/v1/profiles/donor", headers=recent, json=donor_payload(
        "Recent Giver", "O+", *GWALIOR, last_donation_date=str(date.today() - timedelta(days=5)))).status_code == 200

    # SMS: capture what would go to MSG91
    s = get_settings()
    monkeypatch.setattr(s, "sms_provider", "msg91"); monkeypatch.setattr(s, "msg91_authkey", "KEY")
    monkeypatch.setattr(s, "msg91_donor_alert_template_id", "ALERTTPL")
    monkeypatch.setattr(s, "msg91_hospital_accept_template_id", "ACCEPTTPL")
    sent = []
    monkeypatch.setattr(sms, "_post", lambda url, params=None, json=None: sent.append((url, json)) or {"type": "success"})

    r = client.post("/v1/requests", headers=hosp, json={"blood_group": "O+", "units": 2, "urgency": "urgent", "location_text": "City Hospital, Gwalior"})
    assert r.status_code == 201, r.text
    req_id = r.json()["request"]["id"]
    assert len(r.json()["first_wave"]) == 5  # nearest five
    flow = [p for u, p in sent if u.endswith("/flow") and p["template_id"] == "ALERTTPL"]
    assert len(flow) == 1 and len(flow[0]["recipients"]) == 5
    assert flow[0]["recipients"][0]["VAR1"] == "O+" and flow[0]["recipients"][0]["mobiles"].startswith("91")

    mine = client.get("/v1/requests/mine", headers=hosp).json()[0]
    uid = lambda h: client.get("/v1/me", headers=h).json()["user"]["id"]  # noqa: E731
    matched_ids = {m["donor_user_id"] for m in r.json()["request"]["matches"]}
    assert len(matched_ids) == 7  # the 7 nearby compatible donors...
    assert uid(far) not in matched_ids  # ...not Delhi (outside 25 km radius)
    assert uid(wrong) not in matched_ids  # ...not an incompatible blood group
    assert uid(recent) not in matched_ids  # ...not someone who donated 5 days ago (56-day lock)
    assert mine["alerted_pending"] == 5

    # all five decline -> next two queued donors are alerted (and texted)
    for i in range(5):
        # first wave = the 5 nearest = donors 0..4
        assert client.post(f"/v1/requests/{req_id}/decline", headers=donors[i]).status_code == 200
    mine = client.get("/v1/requests/mine", headers=hosp).json()[0]
    assert mine["alerted_pending"] == 2
    assert len([1 for u, p in sent if u.endswith("/flow") and p["template_id"] == "ALERTTPL"]) == 2  # initial + escalation

    # one of the escalated donors accepts -> hospital is texted and sees the donor's contact
    assert client.post(f"/v1/requests/{req_id}/accept", headers=donors[5]).status_code == 200
    assert any(p["template_id"] == "ACCEPTTPL" for u, p in sent if u.endswith("/flow"))
    mine = client.get("/v1/requests/mine", headers=hosp).json()[0]
    assert mine["status"] == "accepted" and mine["accepted_donor"]["phone"] == "7000000005"

    # fulfil -> donor is locked for 56 days
    assert client.post(f"/v1/requests/{req_id}/fulfill", headers=hosp).status_code == 200
    me = client.get("/v1/me", headers=donors[5]).json()
    assert me["donor_profile"]["last_donation_date"] == str(date.today())
    # a second request no longer reaches that donor
    r2 = client.post("/v1/requests", headers=hosp, json={"blood_group": "O+", "units": 1, "urgency": "normal", "location_text": "City Hospital, Gwalior"})
    assert uid(donors[5]) not in {m["donor_user_id"] for m in r2.json()["request"]["matches"]}


def test_map_data_privacy_and_roles(client):
    hosp = login(client, "6000000001", "hospital")
    data = client.get("/v1/map/data", headers=hosp).json()
    assert data["center"] == {"lat": GWALIOR[0], "lng": GWALIOR[1]}
    assert data["summary"]["donors_total"] >= 8
    for d in data["donors"]:
        assert set(d) == {"label", "blood_group", "area", "lat", "lng", "available", "verified", "distance_km"}  # no phone / full name
        assert round(d["lat"], 2) == d["lat"] and round(d["lng"], 2) == d["lng"]  # ~1 km precision only
    assert any(h["is_me"] for h in data["hospitals"])
    donor = login(client, "7000000000", "donor")
    assert client.get("/v1/map/data", headers=donor).status_code == 403
    assert client.get("/v1/requests/city", headers=donor).status_code == 403
    assert client.get("/v1/map/data", headers=login(client, "8888888888", "admin")).status_code == 200


# --------------------------------------------------------------------------- admin
def _admin(client):
    return login(client, "8888888888", "admin")


def _uid(client, headers):
    return client.get("/v1/me", headers=headers).json()["user"]["id"]


def test_suspension_is_enforced_and_reversible(client):
    admin = _admin(client)
    d = login(client, "7100000001", "donor")
    client.post("/v1/profiles/donor", headers=d, json=donor_payload("Sus Pend", "B+", GWALIOR[0], GWALIOR[1]))
    did = _uid(client, d)
    assert client.post(f"/v1/admin/donors/{did}/suspend", headers=admin).status_code == 200
    row = next(r for r in client.get("/v1/admin/donors", headers=admin).json() if r["user_id"] == did)
    assert row["status"] == "suspended" and row["suspended"] is True
    # the donor cannot undo it themselves ...
    assert client.post("/v1/profiles/donor/availability", headers=d, json={"is_available": True}).status_code == 403
    # ... and is not offered to hospitals
    hosp = login(client, "6100000001", "hospital")
    client.post("/v1/profiles/hospital", headers=hosp, json={"facility_name": "H1", "contact_person": "X", "address": "A", "city": "Gwalior", "consent": True, "latitude": GWALIOR[0], "longitude": GWALIOR[1]})
    r = client.post("/v1/requests", headers=hosp, json={"blood_group": "B+", "units": 1, "urgency": "normal", "location_text": "Gwalior"})
    assert did not in {m["donor_user_id"] for m in r.json()["request"]["matches"]}
    assert client.post(f"/v1/admin/donors/{did}/unsuspend", headers=admin).status_code == 200
    assert client.post("/v1/profiles/donor/availability", headers=d, json={"is_available": True}).status_code == 200


def test_verify_and_unverify_donor_and_hospital(client):
    admin = _admin(client)
    d = login(client, "7100000002", "donor")
    client.post("/v1/profiles/donor", headers=d, json=donor_payload("Ver Ify", "A+", GWALIOR[0], GWALIOR[1]))
    did = _uid(client, d)
    get = lambda: next(r for r in client.get("/v1/admin/donors", headers=admin).json() if r["user_id"] == did)  # noqa: E731
    assert get()["verified"] is False
    client.post(f"/v1/admin/donors/{did}/verify", headers=admin)
    assert get()["verified"] is True
    client.post(f"/v1/admin/donors/{did}/unverify", headers=admin)
    assert get()["verified"] is False

    h = login(client, "6100000002", "hospital")
    client.post("/v1/profiles/hospital", headers=h, json={"facility_name": "Verify Me Hospital", "contact_person": "Dr V", "address": "A", "city": "Gwalior", "consent": True})
    hid = _uid(client, h)
    rows = client.get("/v1/admin/hospitals", headers=admin).json()
    me = next(r for r in rows if r["user_id"] == hid)
    assert me["verified"] is False and me["phone"] == "6100000002"
    assert client.get("/v1/admin/hospitals", headers=h).status_code == 403
    client.post(f"/v1/admin/hospitals/{hid}/verify", headers=admin)
    assert next(r for r in client.get("/v1/admin/hospitals", headers=admin).json() if r["user_id"] == hid)["verified"] is True


def test_unverified_hospitals_blocked_only_when_enabled(client, monkeypatch):
    admin = _admin(client)
    h = login(client, "6100000003", "hospital")
    client.post("/v1/profiles/hospital", headers=h, json={"facility_name": "Gate Hospital", "contact_person": "Dr G", "address": "A", "city": "Gwalior", "consent": True, "latitude": GWALIOR[0], "longitude": GWALIOR[1]})
    body = {"blood_group": "O+", "units": 1, "urgency": "normal", "location_text": "Gwalior"}
    assert client.post("/v1/requests", headers=h, json=body).status_code == 201  # default: open
    monkeypatch.setattr(get_settings(), "require_verified_hospitals", True)
    r = client.post("/v1/requests", headers=h, json=body)
    assert r.status_code == 403 and "verification" in r.json()["detail"]
    client.post(f"/v1/admin/hospitals/{_uid(client, h)}/verify", headers=admin)
    assert client.post("/v1/requests", headers=h, json=body).status_code == 201


def test_overview_counts_are_real(client):
    ov = client.get("/v1/admin/overview", headers=_admin(client)).json()
    assert ov["total_donors"] >= 10
    assert ov["available_now"] == sum(ov["available_by_group"].values())
    assert ov["available_now"] + ov["locked"] + ov["offline"] + ov["suspended"] == ov["total_donors"]
    assert ov["hospitals_total"] >= 2 and set(ov["available_by_group"]) == {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}


def test_broadcast_really_sends_to_the_chosen_audience(client, monkeypatch):
    from app.routers import admin as admin_router
    admin = _admin(client)  # log in first: with SMS switched on, OTP login would need an OTP template
    donor_headers = login(client, "7100000003", "donor")
    monkeypatch.setattr(admin_router, "BROADCAST_COOLDOWN_SECONDS", 0)
    s = get_settings()
    monkeypatch.setattr(s, "sms_provider", "msg91"); monkeypatch.setattr(s, "msg91_authkey", "KEY")
    monkeypatch.setattr(s, "msg91_broadcast_template_id", "BCAST")
    sent = []
    monkeypatch.setattr(sms, "_post", lambda url, params=None, json=None: sent.append(json) or {"type": "success"})

    ov = client.get("/v1/admin/overview", headers=admin).json()
    r = client.post("/v1/admin/broadcast", headers=admin, json={"message": "Need O+ donors at City Hospital", "audience": "blood_groups", "blood_groups": ["O+"]})
    assert r.status_code == 200 and r.json()["delivery"] == "queued"
    assert r.json()["recipients"] == ov["available_by_group"]["O+"] > 0
    assert len(sent) == 1 and sent[0]["template_id"] == "BCAST" and len(sent[0]["recipients"]) == r.json()["recipients"]
    assert sent[0]["recipients"][0]["VAR1"] == "Need O+ donors at City Hospital"

    sent.clear()
    r = client.post("/v1/admin/broadcast", headers=admin, json={"message": "Hospitals: drill", "audience": "all_hospitals"})
    assert r.json()["recipients"] >= 2 and len(sent[0]["recipients"]) == r.json()["recipients"]

    # validation and permissions
    assert client.post("/v1/admin/broadcast", headers=admin, json={"message": "x", "audience": "blood_groups", "blood_groups": ["Z+"]}).status_code == 422
    assert client.post("/v1/admin/broadcast", headers=admin, json={"message": "x", "audience": "blood_groups", "blood_groups": []}).status_code == 422
    assert client.post("/v1/admin/broadcast", headers=admin, json={"message": "y" * 161, "audience": "all_available"}).status_code == 422
    assert client.post("/v1/admin/broadcast", headers=donor_headers, json={"message": "x", "audience": "all_available"}).status_code == 403


def test_broadcast_cooldown_and_honest_when_sms_not_configured(client, monkeypatch):
    from app.routers import admin as admin_router
    admin = _admin(client)
    monkeypatch.setattr(admin_router, "BROADCAST_COOLDOWN_SECONDS", 3600)
    s = get_settings()
    monkeypatch.setattr(s, "sms_provider", "none")
    calls = []
    monkeypatch.setattr(sms, "_post", lambda *a, **k: calls.append(1) or {})
    # a broadcast was already sent a moment ago by the previous test -> cooldown applies
    r = client.post("/v1/admin/broadcast", headers=admin, json={"message": "again", "audience": "all_available"})
    assert r.status_code == 429
    monkeypatch.setattr(admin_router, "BROADCAST_COOLDOWN_SECONDS", 0)
    r = client.post("/v1/admin/broadcast", headers=admin, json={"message": "dev", "audience": "all_available"})
    assert r.status_code == 200 and r.json()["delivery"] == "not_sent_sms_not_configured" and not calls


# --------------------------------------------------------------------------- emergency / public
def _audit(action, request_id):
    import json as _json
    from app.database import SessionLocal
    from app.models.audit_log import AuditLog
    db = SessionLocal()
    try:
        rows = db.query(AuditLog).filter(AuditLog.action == action, AuditLog.target_id == request_id).order_by(AuditLog.created_at).all()
        return [_json.loads(r.meta_json or "{}") for r in rows]
    finally:
        db.close()


def _emergency_world(client, tag):
    """Hospital in Chennai (isolated from the other tests' donors) + O+ donors ~3, 15 and 35 km away."""
    hosp = login(client, f"61{tag}000001", "hospital")
    client.post("/v1/profiles/hospital", headers=hosp, json={"facility_name": "ER Hospital", "contact_person": "Dr E", "address": "A", "city": "Chennai", "consent": True, "latitude": CHENNAI[0], "longitude": CHENNAI[1]})
    tokens = {}
    for i, km in enumerate([3, 15, 35]):
        h = login(client, f"72{tag}00000{i}", "donor")
        client.post("/v1/profiles/donor", headers=h, json=donor_payload(f"Emerg Donor{i}", "O+", CHENNAI[0] + km / 111.0, CHENNAI[1]))
        tokens[km] = h
    # geofence of 1 km -> the normal flow finds nobody, so only the emergency can reach the donors
    r = client.post("/v1/requests", headers=hosp, json={"blood_group": "O+", "units": 1, "urgency": "critical", "location_text": "ER Hospital, Chennai", "geofence_km": 1})
    assert r.status_code == 201, r.text
    return hosp, tokens, r.json()["request"]["id"]


def test_emergency_widens_radius_step_by_step(client, monkeypatch):
    from app.routers import emergency
    monkeypatch.setattr(emergency, "WAIT_SECONDS", 0.01)
    hosp, tokens, req_id = _emergency_world(client, "51")
    r = client.post("/v1/emergency/trigger", headers=hosp, json={"request_id": req_id, "is_emergency": True, "latitude": CHENNAI[0], "longitude": CHENNAI[1], "blood_group": "O+"})
    assert r.status_code == 200 and r.json()["started"] is True
    steps = _audit("emergency.radius.alerted", req_id)
    assert [s["radius"] for s in steps] == [5, 10, 20, 40, 50]
    assert [s["count"] for s in steps] == [1, 0, 1, 1, 0]  # 3 km @5, 15 km @20, 35 km @40
    assert _audit("emergency.completed", req_id)
    for km, h in tokens.items():  # every donor can now see the alert
        assert any(x["id"] == req_id for x in client.get("/v1/requests/inbox", headers=h).json()), km


def test_emergency_stops_once_a_donor_accepts(client, monkeypatch):
    from app.routers import emergency
    hosp, tokens, req_id = _emergency_world(client, "52")
    import uuid as _uuid
    uid = _uuid.UUID(req_id)
    me = client.get("/v1/me", headers=hosp).json()["user"]["id"]
    args = dict(request_id=uid, actor_user_id=_uuid.UUID(me), latitude=CHENNAI[0], longitude=CHENNAI[1], blood_group="O+")
    assert emergency._escalation_step(radius=5, first=True, **args) is False  # alerts the 3 km donor
    assert client.post(f"/v1/requests/{req_id}/accept", headers=tokens[3]).status_code == 200
    assert emergency._escalation_step(radius=10, first=False, **args) is True  # sees "accepted" and stops
    assert _audit("emergency.stop", req_id)[0]["reason"] == "request_status_accepted"


def test_emergency_does_not_block_a_worker_thread_while_waiting():
    import inspect
    from app.routers import emergency
    assert inspect.iscoroutinefunction(emergency.run_emergency_escalation)
    assert "time.sleep" not in inspect.getsource(emergency)


def test_public_stats_are_real_and_contain_no_personal_data(client):
    from app.routers import public
    public._cache = None
    r = client.get("/v1/public/stats")  # no login needed
    assert r.status_code == 200
    s = r.json()
    assert set(s) == {"donors_total", "donors_available", "hospitals_total", "requests_fulfilled", "avg_match_seconds"}
    assert s["donors_total"] >= 10 and s["hospitals_total"] >= 3 and s["requests_fulfilled"] >= 1
    assert 0 < s["donors_available"] <= s["donors_total"]
    assert "phone" not in r.text and "name" not in r.text


def test_defaults_work_without_postgres():
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.database_url.startswith("sqlite")
    assert "http://localhost:3000" in s.cors_origin_list and "http://127.0.0.1:5500" in s.cors_origin_list
