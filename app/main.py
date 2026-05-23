from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import DEFAULT_CORS_ORIGIN_REGEX, DEFAULT_CORS_ORIGINS, CAMPAIGNS_DIR, parse_csv_env
from app.database import init_db
from app.routers import campaigns, content, health

app = FastAPI(title="FeedForge API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_csv_env("CORS_ORIGINS", DEFAULT_CORS_ORIGINS),
    allow_origin_regex=os.getenv("CORS_ORIGIN_REGEX", DEFAULT_CORS_ORIGIN_REGEX),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(campaigns.router)
app.include_router(content.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    CAMPAIGNS_DIR.mkdir(parents=True, exist_ok=True)
