from datetime import date
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sanitization import sanitize_optional_text, sanitize_text

VALID_BLOOD_GROUPS = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}


class DonorProfileCreate(BaseModel):
    full_name: str = Field(..., max_length=200)
    age: int = Field(..., ge=18, le=65)
    weight_kg: int = Field(..., ge=50)
    area_text: str = Field(..., max_length=300)
    blood_group: str = Field(..., max_length=5)
    last_donation_date: date | None = None
    medical_notes: str | None = None
    aadhaar_last4: str = Field(..., min_length=4, max_length=4)
    emergency_phone: str = Field(..., max_length=15)
    consent_share: bool = False
    is_available: bool = True
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @field_validator("blood_group")
    @classmethod
    def validate_blood_group(cls, value: str) -> str:
        normalized = value.strip().upper().replace("−", "-")
        if normalized not in VALID_BLOOD_GROUPS:
            raise ValueError(
                "Blood group must be one of A+, A-, B+, B-, AB+, AB-, O+, O-"
            )
        return normalized

    @field_validator("aadhaar_last4")
    @classmethod
    def validate_aadhaar_last4(cls, value: str) -> str:
        if not value.isdigit() or len(value) != 4:
            raise ValueError("Aadhaar last4 must be exactly 4 digits")
        return value

    @field_validator("full_name", "area_text", "emergency_phone")
    @classmethod
    def sanitize_required_texts(cls, value: str) -> str:
        return sanitize_text(value)

    @field_validator("medical_notes")
    @classmethod
    def sanitize_medical_notes(cls, value: str | None) -> str | None:
        return sanitize_optional_text(value)


class DonorPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    area_text: str
    blood_group: str
    age: int
    consent_share: bool


class HospitalProfileCreate(BaseModel):
    facility_name: str = Field(..., max_length=300)
    contact_person: str = Field(..., max_length=200)
    designation: str | None = Field(None, max_length=200)
    facility_type: str | None = Field(None, max_length=120)
    address: str = Field(..., max_length=500)
    city: str = Field(..., max_length=120)
    area: str | None = Field(None, max_length=200)
    gps_text: str | None = Field(None, max_length=200)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    consent: bool = False

    @field_validator("facility_name", "contact_person", "address", "city")
    @classmethod
    def sanitize_required_fields(cls, value: str) -> str:
        return sanitize_text(value)

    @field_validator("designation", "facility_type", "area", "gps_text")
    @classmethod
    def sanitize_optional_fields(cls, value: str | None) -> str | None:
        return sanitize_optional_text(value)


class HospitalPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_name: str
    city: str
    area: str | None
    facility_type: str | None
    verified: bool
