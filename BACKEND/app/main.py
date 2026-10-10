import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.database import init_db
from app.routers import admin, auth, directory, emergency, health, me, profiles, requests
from app.routers import map as map_router
from app.routers import public

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.demo_mode and settings.secret_key in {"dev-only-change-in-production", "change-me", ""}:
        raise RuntimeError("Set a strong SECRET_KEY before running with DEMO_MODE=false")
    try:
        init_db()
    except OperationalError as exc:
        raise RuntimeError(
            "Database unreachable. Check DATABASE_URL in BACKEND/.env. "
            "For local development use DATABASE_URL=sqlite:///./bloodconnect.db "
            "(no server needed); for production use your PostgreSQL URL. "
            "See BACKEND/ENV_GUIDE.md."
        ) from exc
    if not settings.demo_mode and settings.database_url.startswith("sqlite"):
        logging.getLogger(__name__).warning(
            "Running with SQLite and DEMO_MODE=false. On hosts with an ephemeral disk (Render free tier, etc.) "
            "the database is wiped on every deploy - use PostgreSQL in production."
        )
    yield


app = FastAPI(title="BloodBond API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix="/v1")
app.include_router(me.router, prefix="/v1")
app.include_router(profiles.router, prefix="/v1")
app.include_router(directory.router, prefix="/v1")
app.include_router(requests.router, prefix="/v1")
app.include_router(emergency.router, prefix="/v1")
app.include_router(admin.router, prefix="/v1")
app.include_router(map_router.router, prefix="/v1")
app.include_router(public.router, prefix="/v1")
