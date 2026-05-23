from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from fastapi import HTTPException

from app.config import (
    OPENROUTER_MAX_TOKENS,
    OPENROUTER_MODEL,
    OPENROUTER_TIMEOUT_SECONDS,
    OPENROUTER_URL,
)


def extract_json(text: str) -> Any:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start_candidates = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
        if not start_candidates:
            raise
        start = min(start_candidates)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        return json.loads(cleaned[start : end + 1])


async def call_openrouter(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is not set")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:3000"),
        "X-Title": os.getenv("OPENROUTER_APP_NAME", "FeedForge"),
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": OPENROUTER_MAX_TOKENS,
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT_SECONDS) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach OpenRouter: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text
        try:
            error_payload = response.json()
            error = error_payload.get("error") if isinstance(error_payload, dict) else None
            if isinstance(error, dict) and error.get("message"):
                detail = str(error["message"])
        except json.JSONDecodeError:
            pass
        raise HTTPException(status_code=502, detail=f"OpenRouter error: {detail}")
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="OpenRouter returned invalid JSON") from exc

    if isinstance(data, dict) and data.get("error"):
        error = data["error"]
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or json.dumps(error)
        else:
            message = str(error)
        raise HTTPException(status_code=502, detail=f"OpenRouter error: {message}")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        keys = ", ".join(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter response did not include a chat completion. Response keys: {keys}",
        ) from exc
    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail="OpenRouter chat completion content was not text")
    return content
