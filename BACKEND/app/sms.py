"""SMS delivery (MSG91). Provider is chosen with SMS_PROVIDER=none|msg91.

With `none` nothing leaves the server (messages are only logged), which is what you want
for local development. India requires DLT-registered templates; MSG91 handles the
template -> DLT mapping, so you only create the templates in the MSG91 dashboard and put
their ids in the environment (see .env.example).
"""
import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

MSG91_BASE = "https://control.msg91.com/api/v5"
TIMEOUT_SECONDS = 8.0


def is_configured() -> bool:
    s = get_settings()
    return s.sms_provider.lower() == "msg91" and bool(s.msg91_authkey)


def _mask(phone: str) -> str:
    return f"******{phone[-4:]}" if phone else "?"


def _mobile(phone10: str) -> str:
    return f"{get_settings().sms_country_code}{phone10}"


def _post(url: str, *, params: dict | None = None, json: dict | None = None) -> dict:
    """Single network call (kept separate so tests can replace it)."""
    resp = httpx.post(
        url,
        params=params,
        json=json if json is not None else {},
        headers={"authkey": get_settings().msg91_authkey, "accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {}


def _succeeded(body: dict) -> bool:
    # MSG91 can answer HTTP 200 with {"type": "error", ...}, so the body must be checked too.
    return str(body.get("type", "")).lower() != "error"


def send_otp(phone10: str, code: str) -> bool:
    s = get_settings()
    if not is_configured():
        log.info("[sms:dev] OTP for %s not sent (SMS_PROVIDER=%s)", _mask(phone10), s.sms_provider)
        return False
    if not s.msg91_otp_template_id:
        log.error("MSG91_OTP_TEMPLATE_ID is not set")
        return False
    try:
        body = _post(
            f"{MSG91_BASE}/otp",
            params={
                "template_id": s.msg91_otp_template_id,
                "mobile": _mobile(phone10),
                "otp": code,
                "otp_expiry": 10,
            },
        )
    except Exception as exc:  # network error, 4xx/5xx, timeout
        log.error("MSG91 OTP request failed for %s: %s", _mask(phone10), exc)
        return False
    if not _succeeded(body):
        log.error("MSG91 rejected OTP for %s: %s", _mask(phone10), body)
        return False
    return True


def send_flow(template_id: str, recipients: list[tuple[str, dict[str, str]]]) -> int:
    """Send a template SMS to many people. `recipients` = [(phone10, {"VAR1": ..}), ...].

    Returns how many were handed to MSG91 successfully. Never raises.
    """
    if not recipients:
        return 0
    if not is_configured() or not template_id:
        for phone, _vars in recipients:
            log.info("[sms:dev] template %s to %s not sent", template_id or "-", _mask(phone))
        return 0
    payload = {
        "template_id": template_id,
        "short_url": "0",
        "recipients": [{"mobiles": _mobile(phone), **variables} for phone, variables in recipients],
    }
    try:
        body = _post(f"{MSG91_BASE}/flow", json=payload)
    except Exception as exc:
        log.error("MSG91 flow request failed (%d recipients): %s", len(recipients), exc)
        return 0
    if not _succeeded(body):
        log.error("MSG91 rejected flow message: %s", body)
        return 0
    return len(recipients)
