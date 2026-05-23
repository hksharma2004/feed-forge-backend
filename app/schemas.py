from __future__ import annotations

from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    icp: str = Field(min_length=1)
    rules: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)


class ScoreRequest(BaseModel):
    campaign_id: str
    owner_id: str = Field(min_length=1)
    draft: str = Field(min_length=1)


class GenerateRequest(BaseModel):
    campaign_id: str
    owner_id: str = Field(min_length=1)
    brief: str = Field(min_length=1)


class ApproveRequest(BaseModel):
    campaign_id: str
    owner_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    brand_score: float


class RejectRequest(BaseModel):
    campaign_id: str
    owner_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    reason: str = Field(min_length=1)
