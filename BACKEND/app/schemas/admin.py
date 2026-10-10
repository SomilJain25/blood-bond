from __future__ import annotations

from datetime import date, datetime
from typing import Literal
import uuid

from pydantic import BaseModel, Field, field_validator

BLOOD_GROUPS = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}


class AdminOverview(BaseModel):
    total_donors: int
    available_now: int
    active_requests: int
    fulfilled_today: int
    locked: int = 0
    offline: int = 0
    suspended: int = 0
    hospitals_total: int = 0
    hospitals_verified: int = 0
    available_by_group: dict[str, int] = Field(default_factory=dict)


class AdminDonorRow(BaseModel):
    user_id: uuid.UUID
    full_name: str
    blood_group: str
    area_text: str
    status: str = Field(..., description="available|locked|offline|suspended")
    last_donation_date: date | None = None
    verified: bool = False
    suspended: bool = False


class AdminHospitalRow(BaseModel):
    user_id: uuid.UUID
    facility_name: str
    facility_type: str | None = None
    city: str
    contact_person: str
    phone: str
    verified: bool
    created_at: datetime | None = None


class BroadcastRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=160)  # one SMS segment
    audience: Literal["all_available", "blood_groups", "all_hospitals"]
    blood_groups: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("blood_groups")
    @classmethod
    def _valid_groups(cls, v: list[str]) -> list[str]:
        cleaned = [g.strip().upper().replace("\u2212", "-") for g in v]
        bad = [g for g in cleaned if g not in BLOOD_GROUPS]
        if bad:
            raise ValueError(f"unknown blood group(s): {', '.join(bad)}")
        return cleaned


class BroadcastResponse(BaseModel):
    recipients: int
    audience: str
    delivery: Literal["queued", "not_sent_sms_not_configured"]
