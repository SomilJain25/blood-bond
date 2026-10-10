from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sqlalchemy import Float

from app.database import Base

if TYPE_CHECKING:
    from app.models.request_alert import RequestAlert
    from app.models.request_match import RequestMatch


class RequestStatus(str, enum.Enum):
    submitted = "submitted"
    matched = "matched"
    alerted = "alerted"
    accepted = "accepted"
    fulfilled = "fulfilled"
    cancelled = "cancelled"


class RequestUrgency(str, enum.Enum):
    critical = "critical"
    urgent = "urgent"
    normal = "normal"


class BloodRequest(Base):
    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hospital_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    blood_group: Mapped[str] = mapped_column(String(5), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    urgency: Mapped[RequestUrgency] = mapped_column(
        SAEnum(RequestUrgency, name="request_urgency"), nullable=False
    )
    location_text: Mapped[str] = mapped_column(String(300), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RequestStatus] = mapped_column(
        SAEnum(RequestStatus, name="request_status"),
        default=RequestStatus.submitted,
        nullable=False,
        index=True,
    )
    accepted_donor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    alerted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    alerts: Mapped[list["RequestAlert"]] = relationship(
        "RequestAlert",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    matches: Mapped[list["RequestMatch"]] = relationship(
        "RequestMatch",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
