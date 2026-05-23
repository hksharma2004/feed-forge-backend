from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.text_utils import normalize_score

MEMORY_RETENTION_DAYS = 20


def _memory_path(repo_path: Path, filename: str) -> Path:
    return repo_path / "memory" / filename


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _format_memory_entry(content: str, metadata: dict[str, object]) -> str:
    lines = [f"\n---\n{content}"]
    lines.extend(f"({key}: {value})" for key, value in metadata.items())
    return "\n".join(lines)


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
    entries = parse_memory_entries(_read_text(path), "rejected")
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
                _format_memory_entry(
                    item["content"],
                    {
                        "reason": item.get("reason", ""),
                        "created_at": item.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    },
                )
                for item in kept
            ),
            encoding="utf-8",
        )
    return kept


def read_campaign_library(repo_path: Path) -> dict[str, list[dict[str, Any]]]:
    approved = parse_memory_entries(_read_text(_memory_path(repo_path, "approved.md")), "approved")
    rejected = prune_rejected_memory(_memory_path(repo_path, "rejected.md"))
    return {"approved": list(reversed(approved)), "rejected": list(reversed(rejected))}


def append_approved(repo_path: Path, content: str, brand_score: float) -> None:
    approved_path = _memory_path(repo_path, "approved.md")
    with approved_path.open("a", encoding="utf-8") as file:
        file.write(
            _format_memory_entry(
                content,
                {"score": brand_score, "created_at": datetime.now(timezone.utc).isoformat()},
            )
        )


def append_rejected(repo_path: Path, content: str, reason: str) -> None:
    rejected_path = _memory_path(repo_path, "rejected.md")
    prune_rejected_memory(rejected_path)
    with rejected_path.open("a", encoding="utf-8") as file:
        file.write(
            _format_memory_entry(
                content,
                {"reason": reason, "created_at": datetime.now(timezone.utc).isoformat()},
            )
        )
