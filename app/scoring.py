from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from app.agent_files import load_system_prompt
from app.database import get_cached_score, set_cached_score
from app.llm import call_openrouter, extract_json
from app.text_utils import normalize_score

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
