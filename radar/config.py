"""Load the YAML config under config/.

topics.yaml and feeds.yaml are private (what you follow, which feeds you watch) and are
not committed. Locally they live in config/; on GitHub Actions they are passed in through
the TOPICS_YAML / FEEDS_YAML secrets as environment variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "sent.json"


@dataclass
class Config:
    settings: dict
    topics: list[dict]
    feeds: dict
    reader_profile: str = ""
    scoring_notes: str = ""
    arxiv_categories: tuple = ()
    summary_language: str = "English"
    utc_offset_hours: float = 0
    labels: dict = field(default_factory=dict)

    def lookback(self, item_type: str) -> int:
        return int(self.settings["lookback_hours"].get(item_type, 48))


def _read(name: str, env: str | None = None) -> dict:
    if env and os.environ.get(env):
        return yaml.safe_load(os.environ[env]) or {}
    path = ROOT / "config" / name
    if not path.exists():
        example = name.replace(".yaml", ".example.yaml")
        raise FileNotFoundError(
            f"config/{name} is missing: copy config/{example} and edit it locally, "
            f"or set the {env} secret on GitHub Actions")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load() -> Config:
    topics = _read("topics.yaml", "TOPICS_YAML")
    return Config(
        settings=_read("settings.yaml"),
        topics=topics.get("topics", []),
        feeds=_read("feeds.yaml", "FEEDS_YAML"),
        reader_profile=" ".join((topics.get("reader_profile") or "").split()),
        scoring_notes=(topics.get("scoring_notes") or "").strip(),
        arxiv_categories=tuple(topics.get("arxiv_categories") or ()),
        summary_language=topics.get("summary_language") or "English",
        utc_offset_hours=topics.get("utc_offset_hours") or 0,
        labels=topics.get("labels") or {},
    )
