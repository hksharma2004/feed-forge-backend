from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "service": "FeedForge API"}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
