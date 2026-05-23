from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter

from app.agent_files import create_campaign_agent_files
from app.config import CAMPAIGNS_DIR
from app.database import get_campaign, init_db, insert_campaign, list_campaign_rows
from app.memory import append_approved, append_rejected, read_campaign_library
from app.schemas import ApproveRequest, CampaignCreate, RejectRequest

router = APIRouter()


@router.post("/campaign/create")
def create_campaign(body: CampaignCreate) -> dict[str, str]:
    init_db()
    campaign_id = uuid4().hex[:12]
    repo_path = CAMPAIGNS_DIR / campaign_id
    create_campaign_agent_files(repo_path, body)

    created_at = datetime.now(timezone.utc).isoformat()
    insert_campaign(
        campaign_id,
        body.name,
        body.platform,
        str(repo_path.resolve()),
        created_at,
        body.owner_id,
    )
    return {"campaign_id": campaign_id, "name": body.name}


@router.get("/campaigns")
def list_campaigns(owner_id: Optional[str] = None) -> list[dict[str, Any]]:
    return list_campaign_rows(owner_id)


@router.get("/campaign/{campaign_id}/library")
def campaign_library(campaign_id: str, owner_id: str) -> dict[str, list[dict[str, Any]]]:
    campaign = get_campaign(campaign_id, owner_id)
    return read_campaign_library(Path(campaign["repo_path"]))


@router.post("/approve")
def approve(body: ApproveRequest) -> dict[str, bool]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    append_approved(Path(campaign["repo_path"]), body.content, body.brand_score)
    return {"ok": True}


@router.post("/reject")
def reject(body: RejectRequest) -> dict[str, bool]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    append_rejected(Path(campaign["repo_path"]), body.content, body.reason)
    return {"ok": True}
