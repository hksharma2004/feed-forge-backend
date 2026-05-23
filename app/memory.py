from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.text_utils import normalize_score

MEMORY_RETENTION_DAYS = 20


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


def read_campaign_library(repo_path: Path) -> dict[str, list[dict[str, Any]]]:
    memory_dir = repo_path / "memory"
    approved_path = memory_dir / "approved.md"
    rejected_path = memory_dir / "rejected.md"
    approved = parse_memory_entries(approved_path.read_text(encoding="utf-8"), "approved") if approved_path.exists() else []
    rejected = prune_rejected_memory(rejected_path) if rejected_path.exists() else []
    return {"approved": list(reversed(approved)), "rejected": list(reversed(rejected))}


def append_approved(repo_path: Path, content: str, brand_score: float) -> None:
    approved_path = repo_path / "memory" / "approved.md"
    with approved_path.open("a", encoding="utf-8") as file:
        file.write(
            f"\n---\n{content}\n(score: {brand_score})\n(created_at: {datetime.now(timezone.utc).isoformat()})"
        )


def append_rejected(repo_path: Path, content: str, reason: str) -> None:
    rejected_path = repo_path / "memory" / "rejected.md"
    prune_rejected_memory(rejected_path)
    with rejected_path.open("a", encoding="utf-8") as file:
        file.write(f"\n---\n{content}\n(reason: {reason})\n(created_at: {datetime.now(timezone.utc).isoformat()})")
