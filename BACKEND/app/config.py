from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Local default: a SQLite file next to where uvicorn is started (run it from the BACKEND folder).
    # Production: set DATABASE_URL to PostgreSQL, e.g. postgresql+psycopg2://user:pass@host:5432/dbname
    database_url: str = "sqlite:///./bloodconnect.db"
    secret_key: str = "dev-only-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7
    default_geofence_km: float = 25.0

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:5500,http://127.0.0.1:5500"

    # Comma-separated 10-digit phones allowed to sign in as admin (server-side allow-list).
    admin_phones: str = ""

    # SMS (see app/sms.py). SMS_PROVIDER=none keeps everything local; msg91 sends real texts.
    sms_provider: str = "none"
    sms_country_code: str = "91"
    msg91_authkey: str = ""
    msg91_otp_template_id: str = ""
    msg91_donor_alert_template_id: str = ""
    msg91_hospital_accept_template_id: str = ""
    msg91_broadcast_template_id: str = ""  # one variable: VAR1 = the admin's message

    # When true, hospitals must be verified by an admin before they can raise requests.
    require_verified_hospitals: bool = False

    demo_mode: bool = True
    demo_otp_code: str = "123456"

    @property
    def admin_phone_set(self) -> set[str]:
        return {"".join(c for c in p if c.isdigit())[-10:] for p in self.admin_phones.split(",") if p.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
