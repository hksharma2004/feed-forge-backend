from __future__ import annotations

import json
from pathlib import Path

from app.schemas import CampaignCreate


def read_agent_metadata(repo_path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    agent_path = repo_path / "agent.yaml"
    if not agent_path.exists():
        return metadata
    for line in agent_path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def load_system_prompt(repo_path: Path) -> str:
    metadata = read_agent_metadata(repo_path)
    soul = (repo_path / "SOUL.md").read_text(encoding="utf-8") if (repo_path / "SOUL.md").exists() else ""
    rules = (repo_path / "RULES.md").read_text(encoding="utf-8") if (repo_path / "RULES.md").exists() else ""
    name = metadata.get("name", "FeedForge Agent")
    description = metadata.get("description", "")
    return f"# {name}\n\n{description}\n\n{soul}\n\n{rules}".strip()


def create_campaign_agent_files(repo_path: Path, body: CampaignCreate) -> None:
    repo_path.mkdir(parents=True, exist_ok=False)
    (repo_path / "SOUL.md").write_text(
        f"# Identity\n{body.voice}\n\n# Target Audience\n{body.icp}\n",
        encoding="utf-8",
    )
    (repo_path / "RULES.md").write_text(f"# Rules\n{body.rules}\n", encoding="utf-8")
    description = f"FeedForge campaign agent for {body.name} on {body.platform}"
    (repo_path / "agent.yaml").write_text(
        f'spec_version: "0.1.0"\nname: {json.dumps(body.name)}\nversion: 0.1.0\ndescription: {json.dumps(description)}\n',
        encoding="utf-8",
    )
    memory_dir = repo_path / "memory"
    memory_dir.mkdir(exist_ok=True)
    (memory_dir / "approved.md").touch()
    (memory_dir / "rejected.md").touch()
