"""Format the selected items and push them to Telegram (Bot API, HTML mode)."""
from __future__ import annotations

import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from .models import Item

log = logging.getLogger(__name__)
LIMIT = 4096
TYPES = ("paper", "news", "repo")
ICONS = {"paper": "\U0001F4C4", "news": "\U0001F4F0", "repo": "\U0001F4BB"}
# Display strings. Override any of them with `labels:` in topics.yaml.
DEFAULT_LABELS = {
    "title": "Research Radar",
    "paper": "Papers",
    "news": "Products & news",
    "repo": "Open source",
    "featured": "top picks",
    "more": "More",
    "relevance": "relevance",
    "other": "other",
    "empty": "Research Radar: nothing new today",
}


def _topic_label(item: Item, topics: list[dict], other: str) -> str:
    for t in topics:
        if t["id"] == item.topic:
            return t["name"].split(" (")[0]
    return other


def build_messages(featured: list[Item], more: list[Item], cfg) -> list[str]:
    """Featured items are grouped by type (keeping the given order) with a summary; "more"
    items are a compact list of titles. Output is split into messages of at most 4096 chars."""
    e = html.escape
    L = {**DEFAULT_LABELS, **cfg.labels}
    today = (datetime.now(timezone.utc) + timedelta(hours=cfg.utc_offset_hours)).strftime("%Y-%m-%d")
    blocks: list[tuple[str, str]] = [
        ("", f"\U0001F6F0 <b>{e(L['title'])} · {today}</b> ({len(featured)} {e(L['featured'])})")]
    for type_ in TYPES:
        group = [i for i in featured if i.type == type_]
        if not group:
            continue
        blocks.append(("\n\n", f"<b>{ICONS[type_]} {e(L[type_])}</b>"))
        for it in group:
            blocks.append(("\n\n",
                f"<b>[{e(_topic_label(it, cfg.topics, L['other']))}]</b> "
                f"<a href=\"{e(it.url, quote=True)}\">{e(it.title[:300])}</a>\n"
                f"{e(it.summary)}\n<i>{e(it.source)} · {e(L['relevance'])} {it.relevance}</i>"))
    if more:
        blocks.append(("\n\n", f"<b>\U0001F4CE {e(L['more'])} ({len(more)})</b>"))
        for it in more:
            blocks.append(("\n",
                f"{ICONS.get(it.type, '•')} [{e(_topic_label(it, cfg.topics, L['other']))}] "
                f"<a href=\"{e(it.url, quote=True)}\">{e(it.title[:150])}</a>"))
    messages, cur = [], ""
    for sep, b in blocks:
        if cur and len(cur) + len(sep) + len(b) > LIMIT:
            messages.append(cur)
            cur = ""
        cur = f"{cur}{sep}{b}" if cur else b[:LIMIT]
    if cur:
        messages.append(cur)
    return messages


def empty_message(cfg) -> str:
    return f"\U0001F6F0 {html.escape({**DEFAULT_LABELS, **cfg.labels}['empty'])}"


# Substrings that mean the LLM account is out of money (checked case-insensitively).
BILLING_HINTS = ("arrearage", "overdue", "insufficient", "balance", "billing", "payment",
                 "quota", "credit", "http 402")


def is_billing_error(error: str) -> bool:
    low = error.lower()
    return any(h in low for h in BILLING_HINTS)


def failure_message(provider: str, model: str, error: str) -> str:
    """Alert for a run where every LLM batch failed, so a dead radar never goes silent."""
    e = html.escape
    if is_billing_error(error):
        head = "\u26A0\uFE0F <b>Research Radar 需要充值了</b>\n大模型账户欠费或额度用完，本次没有推送。充值后下一次运行会自动恢复。"
    else:
        head = "\u26A0\uFE0F <b>Research Radar 本次运行失败</b>\n大模型调用全部失败，本次没有推送。"
    return f"{head}\n\n<i>{e(provider)} / {e(model)}</i>\n<code>{e(error[:300])}</code>"


def send(messages: list[str]) -> None:
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for text in messages:
        for attempt in (1, 2, 3):
            resp = requests.post(url, timeout=30, json={
                "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            })
            if resp.status_code == 429 and attempt < 3:
                time.sleep(resp.json().get("parameters", {}).get("retry_after", 5) + 1)
                continue
            if not resp.ok:  # never log the URL: it contains the token
                raise RuntimeError(f"Telegram send failed: HTTP {resp.status_code}")
            break
        time.sleep(1)
