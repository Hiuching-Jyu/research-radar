"""Deduplication: URL + title similarity. Sent/seen state lives in data/sent.json."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata

from .config import STATE_PATH
from .models import Item

_STOP = {"the", "a", "an", "of", "for", "and", "to", "in", "on", "with", "from", "by", "is", "its", "at", "as"}


_CJK = "[%s-%s]+" % (chr(0x4E00), chr(0x9FFF))  # CJK unified ideographs


def tokens(text: str) -> frozenset[str]:
    """Comparable token set: words for Latin text, character bigrams for CJK text."""
    t = unicodedata.normalize("NFKC", text).lower()
    out = {w for w in re.findall(r"[a-z0-9]+", t) if w not in _STOP}
    for run in re.findall(_CJK, t):
        out.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return frozenset(out)


def similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_key(item: Item) -> str:
    return hashlib.sha1(" ".join(sorted(tokens(item.title))).encode()).hexdigest()[:16]


def _rank(item: Item, priority: list[str]) -> tuple:
    p = priority.index(item.kind) if item.kind in priority else len(priority)
    return (p, -item.relevance, -len(item.snippet))


def dedupe(items: list[Item], threshold: float, priority: list[str], *, use_event: bool = False) -> list[Item]:
    """Collapse items with the same URL or similar titles, keeping the most authoritative source.
    With use_event=True (after LLM scoring), news items are also merged by the LLM's event label."""
    kept: list[tuple[Item, frozenset, frozenset]] = []
    for item in sorted(items, key=lambda i: _rank(i, priority)):
        tk = tokens(item.title)
        ev = tokens(item.event) if use_event and item.type == "news" else frozenset()
        dup = any(
            item.url == k.url
            or (item.type == k.type and similarity(tk, ktk) >= threshold)
            or (ev and similarity(ev, kev) >= threshold)
            for k, ktk, kev in kept
        )
        if not dup:
            kept.append((item, tk, ev))
    return [k for k, _, _ in kept]


def _salt() -> str:
    """Salt comes from STATE_SALT, falling back to the bot token. Without a salt, anyone
    could hash common words and match them against sent.json."""
    return os.environ.get("STATE_SALT") or os.environ.get("TELEGRAM_BOT_TOKEN") or ""


def salted(digest: str) -> str:
    return hashlib.sha1(f"{_salt()}:{digest}".encode()).hexdigest()[:10]


def _h(text: str) -> str:
    return salted(hashlib.sha1(text.encode()).hexdigest()[:10])


class State:
    """sent: items already pushed. seen: items already scored by the LLM (so we don't pay
    to score them again). Only salted hashes are stored (URL hash, per-token hashes of the
    title), so the file does not reveal what was pushed."""

    def __init__(self, retention_days: int):
        self.retention = retention_days * 86400
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.sent: list[dict] = data.get("sent", [])
        self.seen: dict[str, int] = data.get("seen", {})
        self._sent_urls = {s["u"] for s in self.sent}
        self._sent_tokens = [frozenset(s["t"]) for s in self.sent]

    def is_sent(self, item: Item, threshold: float) -> bool:
        if _h(item.url) in self._sent_urls:
            return True
        tk = frozenset(_h(t) for t in tokens(item.title))
        return any(similarity(tk, s) >= threshold for s in self._sent_tokens)

    def is_seen(self, item: Item) -> bool:
        return salted(title_key(item)) in self.seen

    def mark_seen(self, items: list[Item]) -> None:
        now = int(time.time())
        for i in items:
            self.seen[salted(title_key(i))] = now

    def mark_sent(self, items: list[Item]) -> None:
        now = int(time.time())
        self.sent += [{"u": _h(i.url), "t": sorted(_h(t) for t in tokens(i.title)), "ts": now} for i in items]

    def save(self) -> None:
        cutoff = time.time() - self.retention
        data = {
            "sent": [s for s in self.sent if s["ts"] >= cutoff],
            "seen": {k: v for k, v in self.seen.items() if v >= cutoff},
        }
        STATE_PATH.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
