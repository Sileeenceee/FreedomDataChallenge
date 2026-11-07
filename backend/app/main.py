import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal, engine, init_db
from app.routers import checklists, conversations, evaluations
from app.services.runner import recover_interrupted

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
        checklists.seed_default(session)
    recover_interrupted()
    yield


app = FastAPI(
    title="Freedom Broker CQA API",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
# The UI may be hosted elsewhere (e.g. as a Dynamics 365 web resource), so CORS is configurable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversations.router)
app.include_router(checklists.router)
app.include_router(evaluations.router)


@app.get("/api/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": engine.dialect.name,
        "evaluator": settings.evaluator_mode,
        "model": settings.model_name,
        "effort": settings.cqa_effort,
        "source_api_configured": bool(settings.cqa_api_url),
        "pricing_usd_per_mtok": {"input": settings.price_input, "output": settings.price_output},
    }


# Static UI (plain HTML/CSS/JS) served from the same origin; must be mounted last.
if settings.frontend_dir.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="ui")
