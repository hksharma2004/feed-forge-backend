from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from app.agent_files import load_system_prompt
from app.database import get_cached_score, set_cached_score
from app.llm import call_openrouter, extract_json
from app.text_utils import normalize_score

SIGNAL_WEIGHTS = {
    "favorite_score": 0.09,
    "reply_score": 0.09,
    "repost_score": 0.08,
    "quote_score": 0.06,
    "view_score": 0.06,
    "impression_score": 0.05,
    "engagement_velocity": 0.07,
    "recency_score": 0.05,
    "media_score": 0.04,
    "relevance_score": 0.08,
    "brand_alignment": 0.09,
    "authenticity_score": 0.07,
    "conversation_score": 0.07,
    "profile_authority_score": 0.05,
    "network_overlap_score": 0.04,
    "topic_match_score": 0.06,
    "readability_score": 0.05,
    "hook_strength_score": 0.06,
    "penalty_score": -0.1,
}
SIGNAL_KEYS = list(SIGNAL_WEIGHTS.keys())
X_ALGORITHM_SOURCE = "FeedForge 19-signal X algorithm scoring model"
DISALLOWED_POST_CHARS = {"—": "em dash", "*": "asterisk emphasis"}
GENERIC_PHRASES = [
    "unlock your potential",
    "leverage synergies",
    "revolutionary platform",
    "game changer",
    "10x your",
    "viral thread",
]


def guardrail_violations(text: str, platform: str) -> list[str]:
    lowered = text.lower()
    violations = [f"contains {label}" for char, label in DISALLOWED_POST_CHARS.items() if char in text]
    violations.extend(f"uses generic phrase: {phrase}" for phrase in GENERIC_PHRASES if phrase in lowered)
    if "x/twitter" in platform.lower() and len(text) > 280:
        violations.append("exceeds 280 characters for X/Twitter")
    if len(re.findall(r"#\w+", text)) > 2:
        violations.append("uses more than two hashtags")
    return violations


def normalize_signals(data: dict[str, Any]) -> dict[str, float]:
    raw_signals = data.get("x_algorithm_signals")
    if not isinstance(raw_signals, dict):
        raw_signals = data
    return {key: normalize_score(raw_signals.get(key)) for key in SIGNAL_KEYS}


def apply_guardrail_penalties(signals: dict[str, float], violations: list[str]) -> dict[str, float]:
    if not violations:
        return signals
    penalty = min(0.35, 0.08 * len(violations))
    adjusted = dict(signals)
    adjusted["penalty_score"] = normalize_score(adjusted["penalty_score"] + penalty)
    adjusted["favorite_score"] = normalize_score(adjusted["favorite_score"] - penalty * 0.35)
    adjusted["repost_score"] = normalize_score(adjusted["repost_score"] - penalty * 0.25)
    adjusted["brand_alignment"] = normalize_score(adjusted["brand_alignment"] - penalty * 0.35)
    adjusted["authenticity_score"] = normalize_score(adjusted["authenticity_score"] - penalty * 0.25)
    return adjusted


def weighted_x_score(signals: dict[str, float]) -> float:
    positive_sum = sum(weight for weight in SIGNAL_WEIGHTS.values() if weight > 0)
    negative_sum = sum(abs(weight) for weight in SIGNAL_WEIGHTS.values() if weight < 0)
    raw = sum(signals[key] * weight for key, weight in SIGNAL_WEIGHTS.items())
    normalized = (raw + negative_sum) / (positive_sum + negative_sum)
    return normalize_score(normalized)


def signal_rows(signals: dict[str, float], violations: list[str]) -> list[dict[str, Any]]:
    rows = [
        {
            "metric": key,
            "characteristic": key,
            "value": signals[key],
            "source": X_ALGORITHM_SOURCE,
        }
        for key in SIGNAL_KEYS
    ]
    if violations:
        rows.append(
            {
                "metric": "Guardrails",
                "characteristic": "Deterministic post filter: " + "; ".join(violations),
                "value": 1.0,
                "source": "FeedForge guardrails",
            }
        )
    return rows


def add_legacy_score_fields(scores: dict[str, Any]) -> dict[str, Any]:
    signals = scores.get("x_algorithm_signal_scores")
    if not isinstance(signals, dict):
        signals = {}

    positive_scores = [
        scores.get("brand_score"),
        scores.get("like"),
        scores.get("reply"),
        scores.get("repost"),
        scores.get("quote"),
        scores.get("view"),
        scores.get("impression"),
    ]
    click_values = [normalize_score(value) for value in positive_scores]
    scores["click"] = normalize_score(sum(click_values) / len(click_values)) if click_values else 0.0

    penalty = normalize_score(signals.get("penalty_score", scores.get("block")))
    authenticity_gap = 1.0 - normalize_score(signals.get("authenticity_score", 1.0))
    relevance_gap = 1.0 - normalize_score(signals.get("relevance_score", 1.0))
    scores["block"] = penalty
    scores["mute"] = normalize_score((penalty + authenticity_gap + relevance_gap) / 3)
    return scores


def is_current_score(scores: dict[str, Any]) -> bool:
    signals = scores.get("x_algorithm_signal_scores")
    return isinstance(signals, dict) and all(key in signals for key in SIGNAL_KEYS)


def normalize_scores(data: dict[str, Any]) -> dict[str, Any]:
    signals = normalize_signals(data)
    violations = data.get("_guardrail_violations", [])
    if not isinstance(violations, list):
        violations = []
    signals = apply_guardrail_penalties(signals, [str(item) for item in violations])
    scores = {
        "like": signals["favorite_score"],
        "reply": signals["reply_score"],
        "repost": signals["repost_score"],
        "quote": signals["quote_score"],
        "view": signals["view_score"],
        "impression": signals["impression_score"],
    }
    scores["brand_score"] = weighted_x_score(signals)
    scores["x_algorithm_signal_scores"] = signals
    scores["x_algorithm_signals"] = signal_rows(signals, [str(item) for item in violations])
    rewrites = data.get("rewrites", [])
    scores["rewrites"] = [str(item) for item in rewrites[:2]]
    while len(scores["rewrites"]) < 2:
        scores["rewrites"].append("Improve the weakest X algorithm signal with a clearer hook, stronger reply intent, or lower penalty risk.")
    return add_legacy_score_fields(scores)


async def score_draft(campaign: dict[str, Any], draft: str, system_prompt: Optional[str] = None) -> dict[str, Any]:
    campaign_id: str = campaign["id"]
    cached = get_cached_score(campaign_id, draft)
    if cached is not None and is_current_score(cached):
        add_legacy_score_fields(cached)
        set_cached_score(campaign_id, draft, cached)
        return cached

    prompt = system_prompt or load_system_prompt(Path(campaign["repo_path"]))
    platform = campaign["platform"]
    signal_keys = ", ".join(SIGNAL_KEYS)
    scoring_prompt = f"""Score this post for {platform} using only these 19 X algorithm signals.
Return ONLY valid JSON with this exact structure:
{{ "x_algorithm_signals": {{ "favorite_score": 0.0, "reply_score": 0.0, "repost_score": 0.0, "quote_score": 0.0, "view_score": 0.0, "impression_score": 0.0, "engagement_velocity": 0.0, "recency_score": 0.0, "media_score": 0.0, "relevance_score": 0.0, "brand_alignment": 0.0, "authenticity_score": 0.0, "conversation_score": 0.0, "profile_authority_score": 0.0, "network_overlap_score": 0.0, "topic_match_score": 0.0, "readability_score": 0.0, "hook_strength_score": 0.0, "penalty_score": 0.0 }}, "rewrites": [string, string] }}

Use exactly these signal names and no others:
{signal_keys}.

Score every signal from 0.0 to 1.0. Higher penalty_score means more risk. Do not return brand_score; FeedForge computes it. rewrites must name the weakest signal being improved. Avoid asterisks and em dashes in rewrites.

Post to score: {draft}"""
    raw = await call_openrouter(prompt, scoring_prompt)
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Scoring response was not a JSON object")
    parsed["_guardrail_violations"] = guardrail_violations(draft, platform)
    scores = normalize_scores(parsed)
    set_cached_score(campaign_id, draft, scores)
    return scores
