from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.database import get_campaign
from app.generation import generate_posts
from app.schemas import GenerateRequest, ScoreRequest
from app.scoring import score_draft

router = APIRouter()


@router.post("/score")
async def score(body: ScoreRequest) -> dict[str, Any]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    return await score_draft(campaign, body.draft)


@router.post("/generate")
async def generate(body: GenerateRequest) -> list[dict[str, Any]]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    return await generate_posts(campaign, body.brief)
