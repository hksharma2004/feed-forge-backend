from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.agent_files import load_system_prompt
from app.llm import call_openrouter, extract_json
from app.scoring import score_draft
from app.text_utils import compact_text, sanitize_post_text


def _load_memory(repo_path: Path, filename: str) -> str:
    path = repo_path / "memory" / filename
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _post_content(item: Any) -> str:
    if isinstance(item, str):
        return sanitize_post_text(item)
    if isinstance(item, dict):
        content = item.get("content") or item.get("post") or item.get("text")
        if isinstance(content, str) and content.strip():
            return sanitize_post_text(content)
        raise HTTPException(status_code=502, detail="Generated post item did not include content")
    raise HTTPException(status_code=502, detail="Generated post item was not a string or object")


async def generate_posts(campaign: dict[str, Any], brief_text: str) -> list[dict[str, Any]]:
    repo_path = Path(campaign["repo_path"])
    approved = _load_memory(repo_path, "approved.md")
    rejected = _load_memory(repo_path, "rejected.md")
    system_prompt = compact_text(load_system_prompt(repo_path), 5000)
    brief = compact_text(brief_text, 1200)
    approved_examples = compact_text(approved, 1200) or "No approved examples yet."
    rejected_examples = compact_text(rejected, 900) or "No rejected examples yet."
    platform = campaign["platform"]
    generate_prompt = f"""Generate 3 distinct social media post variants for {platform}.

Return ONLY valid JSON as an array of objects with this exact structure:
[
  {{"content": "complete ready-to-publish post"}}
]

Keep each post under 280 characters unless the platform normally requires longer copy.
Do not use em dashes. Do not use asterisks or markdown emphasis. Do not use generic phrases such as unlock your potential, leverage synergies, revolutionary platform, game changer, 10x your, or viral thread.
Generate concrete posts with clear audience value, reply intent, repost or quote intent, strong hook strength, brand alignment, authenticity, readability, and low penalty risk.
Make the response compact. No markdown, no explanations outside the JSON.

Brief: {brief}

Approved style examples: {approved_examples}
Rejected patterns to avoid: {rejected_examples}"""
    raw = await call_openrouter(system_prompt, generate_prompt)
    parsed = extract_json(raw)
    if not isinstance(parsed, list):
        raise HTTPException(status_code=502, detail="Generation response was not a JSON array")
    if not parsed:
        raise HTTPException(status_code=502, detail="Generation response did not include any posts")
    variants = [_post_content(item) for item in parsed[:3]]
    item_scores_list = await asyncio.gather(*(score_draft(campaign, content, system_prompt) for content in variants))
    scored = [{"content": content, "scores": item_scores} for content, item_scores in zip(variants, item_scores_list)]
    scored.sort(key=lambda item: item["scores"]["brand_score"], reverse=True)
    return scored[:3]
