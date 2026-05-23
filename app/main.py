from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", APP_ROOT / "data"))
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "feedforge.db"))
CAMPAIGNS_DIR = Path(os.getenv("CAMPAIGNS_DIR", DATA_DIR / "campaigns"))
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/owl-alpha")
OPENROUTER_TIMEOUT_SECONDS = float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "45"))
OPENROUTER_MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "2200"))

X_ACTION_WEIGHTS = {
    "favorite_score": 0.18,
    "reply_score": 0.15,
    "repost_score": 0.13,
    "photo_expand_score": 0.05,
    "click_score": 0.1,
    "profile_click_score": 0.06,
    "vqv_score": 0.06,
    "share_score": 0.1,
    "share_via_dm_score": 0.06,
    "share_via_copy_link_score": 0.06,
    "dwell_score": 0.08,
    "quote_score": 0.09,
    "quoted_click_score": 0.04,
    "follow_author_score": 0.06,
    "not_interested_score": -0.16,
    "block_author_score": -0.24,
    "mute_author_score": -0.2,
    "report_score": -0.25,
    "dwell_time": 0.04,
}
X_ACTION_KEYS = list(X_ACTION_WEIGHTS.keys())
X_ALGORITHM_SOURCE = "xai-org/x-algorithm: phoenix/runners.py ACTIONS and home-mixer/scorers/ranking_scorer.rs"
DISALLOWED_POST_CHARS = {"—": "em dash", "*": "asterisk emphasis"}
GENERIC_PHRASES = [
    "unlock your potential",
    "leverage synergies",
    "revolutionary platform",
    "game changer",
    "10x your",
    "viral thread",
]


def parse_csv_env(name: str, fallback: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return fallback
    return [item.strip() for item in value.split(",") if item.strip()]


app = FastAPI(title="FeedForge API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_csv_env(
        "CORS_ORIGINS",
        ["http://localhost:3000", "http://127.0.0.1:3000"],
    ),
    allow_origin_regex=os.getenv(
        "CORS_ORIGIN_REGEX",
        r"https?://(localhost|127\.0\.0\.1):\d+",
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "service": "FeedForge API"}


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


MEMORY_RETENTION_DAYS = 20


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                name TEXT,
                platform TEXT,
                repo_path TEXT,
                created_at TEXT,
                owner_id TEXT
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE campaigns ADD COLUMN owner_id TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS score_cache (
                cache_key TEXT PRIMARY KEY,
                scores_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _score_cache_key(campaign_id: str, content: str) -> str:
    """SHA-256 of campaign_id + normalised content so the key is stable."""
    normalised = re.sub(r"\s+", " ", content).strip()
    return hashlib.sha256(f"{campaign_id}:{normalised}".encode()).hexdigest()


def get_cached_score(campaign_id: str, content: str) -> Optional[dict[str, Any]]:
    key = _score_cache_key(campaign_id, content)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT scores_json FROM score_cache WHERE cache_key = ?", (key,)
        ).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None
    return None


def set_cached_score(campaign_id: str, content: str, scores: dict[str, Any]) -> None:
    key = _score_cache_key(campaign_id, content)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO score_cache (cache_key, scores_json, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(scores), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    CAMPAIGNS_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def get_campaign(campaign_id: str, owner_id: Optional[str] = None) -> dict[str, Any]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if owner_id:
            row = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns WHERE id = ? AND owner_id = ?",
                (campaign_id, owner_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return dict(row)


def run_gitagent(args: list[str], repo_path: Path, capture: bool = False) -> str:
    try:
        result = subprocess.run(
            ["gitagent", *args],
            cwd=repo_path,
            capture_output=capture,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="gitagent CLI is not installed. Install @open-gitagent/gitagent and ensure gitagent is on PATH.",
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise HTTPException(status_code=500, detail=f"gitagent failed: {detail}") from exc
    return result.stdout if capture else ""


def load_system_prompt(repo_path: Path) -> str:
    return run_gitagent(["export", "--format", "system-prompt"], repo_path, capture=True).strip()


def compact_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def parse_memory_entries(text: str, kind: str) -> list[dict[str, Any]]:
    entries = []
    for block in [item.strip() for item in text.split("---") if item.strip()]:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        metadata: dict[str, str] = {}
        content_lines = []
        for line in lines:
            if line.startswith("(") and line.endswith(")") and ":" in line:
                key, value = line[1:-1].split(":", 1)
                metadata[key.strip()] = value.strip()
            else:
                content_lines.append(line)
        content = "\n".join(content_lines).strip()
        if not content:
            continue
        item: dict[str, Any] = {
            "id": uuid4().hex[:12],
            "content": content,
            "created_at": metadata.get("created_at"),
        }
        if kind == "approved":
            item["brand_score"] = normalize_score(metadata.get("score"))
        else:
            item["reason"] = metadata.get("reason", "")
        entries.append(item)
    return entries


def prune_rejected_memory(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = parse_memory_entries(text, "rejected")
    cutoff = datetime.now(timezone.utc) - timedelta(days=MEMORY_RETENTION_DAYS)
    kept = []
    for item in entries:
        created_at = item.get("created_at")
        if created_at:
            try:
                if datetime.fromisoformat(created_at) < cutoff:
                    continue
            except ValueError:
                pass
        kept.append(item)
    if len(kept) != len(entries):
        path.write_text(
            "".join(
                f"\n---\n{item['content']}\n(reason: {item.get('reason', '')})\n(created_at: {item.get('created_at') or datetime.now(timezone.utc).isoformat()})"
                for item in kept
            ),
            encoding="utf-8",
        )
    return kept


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


def normalize_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def sanitize_post_text(text: str) -> str:
    cleaned = text.replace("—", " - ").replace("*", "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"^[A-Z][A-Za-z ]{2,30}:\s+", "", cleaned.strip())
    return cleaned.strip()


def guardrail_violations(text: str, platform: str) -> list[str]:
    lowered = text.lower()
    violations = [f"contains {label}" for char, label in DISALLOWED_POST_CHARS.items() if char in text]
    violations.extend(f"uses generic phrase: {phrase}" for phrase in GENERIC_PHRASES if phrase in lowered)
    if "x/twitter" in platform.lower() and len(text) > 280:
        violations.append("exceeds 280 characters for X/Twitter")
    if len(re.findall(r"#\w+", text)) > 2:
        violations.append("uses more than two hashtags")
    return violations


def normalize_x_actions(data: dict[str, Any]) -> dict[str, float]:
    raw_actions = data.get("x_action_scores")
    if not isinstance(raw_actions, dict):
        raw_actions = data
    return {key: normalize_score(raw_actions.get(key)) for key in X_ACTION_KEYS}


def apply_guardrail_penalties(actions: dict[str, float], violations: list[str]) -> dict[str, float]:
    if not violations:
        return actions
    penalty = min(0.35, 0.08 * len(violations))
    adjusted = dict(actions)
    adjusted["not_interested_score"] = normalize_score(adjusted["not_interested_score"] + penalty)
    adjusted["mute_author_score"] = normalize_score(adjusted["mute_author_score"] + penalty * 0.75)
    adjusted["report_score"] = normalize_score(adjusted["report_score"] + penalty * 0.35)
    adjusted["favorite_score"] = normalize_score(adjusted["favorite_score"] - penalty * 0.35)
    adjusted["share_score"] = normalize_score(adjusted["share_score"] - penalty * 0.35)
    return adjusted


def weighted_x_score(actions: dict[str, float]) -> float:
    positive_sum = sum(weight for weight in X_ACTION_WEIGHTS.values() if weight > 0)
    negative_sum = sum(abs(weight) for weight in X_ACTION_WEIGHTS.values() if weight < 0)
    raw = sum(actions[key] * weight for key, weight in X_ACTION_WEIGHTS.items())
    normalized = (raw + negative_sum) / (positive_sum + negative_sum)
    return normalize_score(normalized)


def grouped_score(actions: dict[str, float], keys: list[str]) -> float:
    if not keys:
        return 0.0
    score = sum(actions[key] for key in keys) / len(keys)
    return normalize_score(score)


def x_algorithm_signals(actions: dict[str, float], violations: list[str]) -> list[dict[str, Any]]:
    signals = [
        {
            "metric": "Like",
            "characteristic": "favorite_score: Phoenix probability that the viewer favorites the post.",
            "value": actions["favorite_score"],
            "source": X_ALGORITHM_SOURCE,
        },
        {
            "metric": "Reply",
            "characteristic": "reply_score: Phoenix probability that the viewer replies.",
            "value": actions["reply_score"],
            "source": X_ALGORITHM_SOURCE,
        },
        {
            "metric": "Repost",
            "characteristic": "repost_score, quote_score, and share scores: retweet, quote, DM share, or copy-link share intent.",
            "value": grouped_score(actions, ["repost_score", "quote_score", "share_score", "share_via_dm_score", "share_via_copy_link_score"]),
            "source": X_ALGORITHM_SOURCE,
        },
        {
            "metric": "Click",
            "characteristic": "click_score, profile_click_score, photo_expand_score, quoted_click_score, dwell_score, dwell_time, and vqv_score.",
            "value": grouped_score(actions, ["click_score", "profile_click_score", "photo_expand_score", "quoted_click_score", "dwell_score", "dwell_time", "vqv_score"]),
            "source": X_ALGORITHM_SOURCE,
        },
        {
            "metric": "Block",
            "characteristic": "block_author_score and report_score: negative feedback that pushes content down.",
            "value": grouped_score(actions, ["block_author_score", "report_score"]),
            "source": X_ALGORITHM_SOURCE,
        },
        {
            "metric": "Mute",
            "characteristic": "mute_author_score and not_interested_score: soft negative feedback and dislike risk.",
            "value": grouped_score(actions, ["mute_author_score", "not_interested_score"]),
            "source": X_ALGORITHM_SOURCE,
        },
    ]
    if violations:
        signals.append(
            {
                "metric": "Guardrails",
                "characteristic": "Deterministic post filter: " + "; ".join(violations),
                "value": 1.0,
                "source": "FeedForge guardrails plus xai-org/x-algorithm negative feedback signals",
            }
        )
    return signals


def normalize_scores(data: dict[str, Any]) -> dict[str, Any]:
    actions = normalize_x_actions(data)
    violations = data.get("_guardrail_violations", [])
    if not isinstance(violations, list):
        violations = []
    actions = apply_guardrail_penalties(actions, [str(item) for item in violations])
    scores = {
        "like": actions["favorite_score"],
        "reply": actions["reply_score"],
        "repost": grouped_score(actions, ["repost_score", "quote_score", "share_score", "share_via_dm_score", "share_via_copy_link_score"]),
        "click": grouped_score(actions, ["click_score", "profile_click_score", "photo_expand_score", "quoted_click_score", "dwell_score", "dwell_time", "vqv_score"]),
        "block": grouped_score(actions, ["block_author_score", "report_score"]),
        "mute": grouped_score(actions, ["mute_author_score", "not_interested_score"]),
    }
    scores["brand_score"] = weighted_x_score(actions)
    scores["x_action_scores"] = actions
    scores["x_algorithm_signals"] = x_algorithm_signals(actions, [str(item) for item in violations])
    rewrites = data.get("rewrites", [])
    scores["rewrites"] = [str(item) for item in rewrites[:2]]
    while len(scores["rewrites"]) < 2:
        scores["rewrites"].append("Improve the weakest X-algorithm signal with a clearer hook, stronger reply intent, or lower negative-feedback risk.")
    return scores


async def score_draft(campaign: dict[str, Any], draft: str, system_prompt: Optional[str] = None) -> dict[str, Any]:
    campaign_id: str = campaign["id"]
    cached = get_cached_score(campaign_id, draft)
    if cached is not None:
        return cached

    prompt = system_prompt or load_system_prompt(Path(campaign["repo_path"]))
    platform = campaign["platform"]
    scoring_prompt = f"""Score this post for {platform} using only the action names present in xai-org/x-algorithm.
Return ONLY valid JSON with this exact structure:
{{ "x_action_scores": {{ "favorite_score": 0.0, "reply_score": 0.0, "repost_score": 0.0, "photo_expand_score": 0.0, "click_score": 0.0, "profile_click_score": 0.0, "vqv_score": 0.0, "share_score": 0.0, "share_via_dm_score": 0.0, "share_via_copy_link_score": 0.0, "dwell_score": 0.0, "quote_score": 0.0, "quoted_click_score": 0.0, "follow_author_score": 0.0, "not_interested_score": 0.0, "block_author_score": 0.0, "mute_author_score": 0.0, "report_score": 0.0, "dwell_time": 0.0 }}, "rewrites": [string, string] }}

Measure these exact X-algorithm characteristics from phoenix/runners.py and home-mixer/scorers/ranking_scorer.rs:
favorite_score, reply_score, repost_score, photo_expand_score, click_score, profile_click_score, vqv_score, share_score, share_via_dm_score, share_via_copy_link_score, dwell_score, quote_score, quoted_click_score, follow_author_score, not_interested_score, block_author_score, mute_author_score, report_score, dwell_time.

Do not invent metrics. Do not return brand_score; FeedForge computes it deterministically with the weighted scorer shape from the X repo. rewrites must name the lowest X action signal being improved. Avoid asterisks and em dashes in rewrites.

Post to score: {draft}"""
    raw = await call_openrouter(prompt, scoring_prompt)
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Scoring response was not a JSON object")
    parsed["_guardrail_violations"] = guardrail_violations(draft, platform)
    scores = normalize_scores(parsed)
    set_cached_score(campaign_id, draft, scores)
    return scores


@app.post("/campaign/create")
def create_campaign(body: CampaignCreate) -> dict[str, str]:
    init_db()
    campaign_id = uuid4().hex[:12]
    repo_path = CAMPAIGNS_DIR / campaign_id
    repo_path.mkdir(parents=True, exist_ok=False)
    run_gitagent(["init", "--template", "minimal"], repo_path)

    (repo_path / "SOUL.md").write_text(
        f"# Identity\n{body.voice}\n\n# Target Audience\n{body.icp}\n",
        encoding="utf-8",
    )
    (repo_path / "RULES.md").write_text(f"# Rules\n{body.rules}\n", encoding="utf-8")
    description = f"FeedForge campaign agent for {body.name} on {body.platform}"
    (repo_path / "agent.yaml").write_text(
        f"name: {json.dumps(body.name)}\ndescription: {json.dumps(description)}\n",
        encoding="utf-8",
    )
    memory_dir = repo_path / "memory"
    memory_dir.mkdir(exist_ok=True)
    (memory_dir / "approved.md").touch()
    (memory_dir / "rejected.md").touch()

    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO campaigns (id, name, platform, repo_path, created_at, owner_id) VALUES (?, ?, ?, ?, ?, ?)",
            (campaign_id, body.name, body.platform, str(repo_path.resolve()), created_at, body.owner_id),
        )
        conn.commit()
    return {"campaign_id": campaign_id, "name": body.name}


@app.get("/campaigns")
def list_campaigns(owner_id: Optional[str] = None) -> list[dict[str, Any]]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if owner_id:
            rows = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns WHERE owner_id = ? ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, platform, repo_path, created_at, owner_id FROM campaigns ORDER BY created_at DESC"
            ).fetchall()
    return [dict(row) for row in rows]


@app.post("/score")
async def score(body: ScoreRequest) -> dict[str, Any]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    return await score_draft(campaign, body.draft)


@app.post("/generate")
async def generate(body: GenerateRequest) -> list[dict[str, Any]]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    repo_path = Path(campaign["repo_path"])
    approved = (repo_path / "memory" / "approved.md").read_text(encoding="utf-8")
    rejected = (repo_path / "memory" / "rejected.md").read_text(encoding="utf-8")
    system_prompt = compact_text(load_system_prompt(repo_path), 5000)
    brief = compact_text(body.brief, 1200)
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
Generate concrete posts with clear audience value, reply intent, repost/share intent, click/dwell intent, and low not_interested/block/mute/report risk.
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
    variants: list[str] = []
    for item in parsed[:3]:
        if isinstance(item, str):
            variants.append(sanitize_post_text(item))
        elif isinstance(item, dict):
            content = item.get("content") or item.get("post") or item.get("text")
            if not isinstance(content, str) or not content.strip():
                raise HTTPException(status_code=502, detail="Generated post item did not include content")
            variants.append(sanitize_post_text(content))
        else:
            raise HTTPException(status_code=502, detail="Generated post item was not a string or object")
    item_scores_list = await asyncio.gather(*(score_draft(campaign, content, system_prompt) for content in variants))
    scored = [{"content": content, "scores": item_scores} for content, item_scores in zip(variants, item_scores_list)]
    scored.sort(key=lambda item: item["scores"]["brand_score"], reverse=True)
    return scored[:3]


@app.get("/campaign/{campaign_id}/library")
def campaign_library(campaign_id: str, owner_id: str) -> dict[str, list[dict[str, Any]]]:
    campaign = get_campaign(campaign_id, owner_id)
    memory_dir = Path(campaign["repo_path"]) / "memory"
    approved_path = memory_dir / "approved.md"
    rejected_path = memory_dir / "rejected.md"
    approved = parse_memory_entries(approved_path.read_text(encoding="utf-8"), "approved") if approved_path.exists() else []
    rejected = prune_rejected_memory(rejected_path) if rejected_path.exists() else []
    return {"approved": list(reversed(approved)), "rejected": list(reversed(rejected))}


@app.post("/approve")
def approve(body: ApproveRequest) -> dict[str, bool]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    approved_path = Path(campaign["repo_path"]) / "memory" / "approved.md"
    with approved_path.open("a", encoding="utf-8") as file:
        file.write(
            f"\n---\n{body.content}\n(score: {body.brand_score})\n(created_at: {datetime.now(timezone.utc).isoformat()})"
        )
    return {"ok": True}


@app.post("/reject")
def reject(body: RejectRequest) -> dict[str, bool]:
    campaign = get_campaign(body.campaign_id, body.owner_id)
    rejected_path = Path(campaign["repo_path"]) / "memory" / "rejected.md"
    prune_rejected_memory(rejected_path)
    with rejected_path.open("a", encoding="utf-8") as file:
        file.write(f"\n---\n{body.content}\n(reason: {body.reason})\n(created_at: {datetime.now(timezone.utc).isoformat()})")
    return {"ok": True}
