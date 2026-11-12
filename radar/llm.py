"""LLM relevance scoring + one-line summaries.

Environment variables:
  LLM_PROVIDER  anthropic | github_models | openai_compatible (defaults to llm.provider in
                settings.yaml; if anthropic is selected but ANTHROPIC_API_KEY is missing,
                falls back to openai_compatible or github_models when their keys are present)
  LLM_MODEL     model name (defaults below)
  LLM_BASE_URL / LLM_API_KEY   openai_compatible only: any service that speaks the OpenAI
                chat/completions API (DeepSeek, DashScope/Qwen, OpenRouter, a local Ollama, ...)
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from . import http
from .dedup import tokens
from .models import Item

log = logging.getLogger(__name__)

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "github_models": "openai/gpt-4o-mini",
                  "openai_compatible": "deepseek-chat"}
DEFAULT_BASE_URL = "https://api.deepseek.com"
GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
# models that support server-side refusal fallbacks
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "relevance": {"type": "integer"},
                    "topic": {"type": "string"},
                    "summary": {"type": "string"},
                    "event": {"type": "string"},
                },
                "required": ["id", "relevance", "topic", "summary", "event"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

CURATE_SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}},
    "required": ["groups"],
    "additionalProperties": False,
}

CURATE_PROMPT = """The JSON array below holds news items that already passed a first relevance filter.
Reader: {reader}

Do two things:

1. Merge: put items that report the same event into one group (the same product or model launch,
   the same paper, the same report, the same funding round; coverage by different outlets or in
   different languages still counts as the same event). An item with no duplicate is a group of one.
   Items that merely share a theme but are about different companies, products or papers are NOT
   duplicates and must not share a group. Most items should end up alone.
2. Rank: order the groups from most to least valuable for the reader. Priority: a concrete new
   hardware product / dataset / open-source project > a paper with a new method > a model release
   from a large company > commentary, funding news, market data. Try to make the first 10 groups
   cover different topics rather than one kind of content.

Inside each group, put the most authoritative and complete source first (official blogs and
specialist media before reposts and finance news sites).
Every id must appear in exactly one group. Output a single JSON object and nothing else:
{{"groups": [[id, ...], [id], ...]}}"""


def _system_prompt(cfg) -> str:
    topic_text = "\n".join(
        f"- {t['id']} ({t['name']}): {' '.join(t['description'].split())}" for t in cfg.topics)
    notes = ""
    if cfg.scoring_notes:
        notes = "\n  Additional notes:\n" + "\n".join(f"  {line}" for line in cfg.scoring_notes.splitlines())
    return f"""You are filtering a daily feed of new papers, products, open-source projects and industry news for one reader.
Reader: {cfg.reader_profile or 'an engineer who follows the topics below'}

Topics:
{topic_text}

The input is a JSON array; each element has id, type (paper/news/repo), source, title, snippet.
For some items the snippet is only the search query that found them; judge those mainly by the title.

For every item output:
- id: unchanged
- relevance: integer 1-10. Be strict; most items should score below 6.
  9-10 = squarely on a topic and concrete: a new product, hardware, dataset, open-source project or
         method the reader will almost certainly want to open;
  7-8  = clearly on a topic with concrete content, worth a look;
  4-6  = tangential: general industry news, funding, commentary;
  1-3  = unrelated.
  Cap at 5: stock-market coverage, listed companies answering investor questions, analyst-style
  sector notes, trade-show press releases, corporate strategy announcements, coverage of forums and
  competitions, hiring, marketing pieces.{notes}
- topic: the id of the best matching topic (must be one of the ids above; "none" if unrelated)
- summary: one sentence in {cfg.summary_language} (about 60 characters at most) saying what it is and
  why it is worth a look. Use only what the title and snippet explicitly say. Do not speculate: no
  "may", "suggests", "likely", and no model numbers, figures or use cases that are not in the input.
  If all you have is a title, restate the title clearly in {cfg.summary_language}. Proper nouns may
  stay in their original language. May be an empty string when relevance < 6.
- event: a short lowercase English label for the event this item reports; it must contain the
  company, product or project name, e.g. "acme x1 sensor launch". Items reporting the same event
  must get the same label.

Output a single JSON object and nothing else: {{"items": [...]}}"""


def _payload(batch: list[Item]) -> str:
    return json.dumps(
        [{"id": i, "type": it.type, "source": it.source, "title": it.title, "snippet": it.snippet}
         for i, it in enumerate(batch)],
        ensure_ascii=False,
    )


class Scorer:
    def __init__(self, cfg):
        conf = cfg.settings["llm"]
        provider = os.environ.get("LLM_PROVIDER") or conf.get("provider", "anthropic")
        if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            if os.environ.get("LLM_API_KEY"):
                provider = "openai_compatible"
            elif self._github_token():
                log.warning("no ANTHROPIC_API_KEY, falling back to GitHub Models")
                provider = "github_models"
        if provider not in DEFAULT_MODELS:
            raise ValueError(f"unknown LLM_PROVIDER: {provider}")
        self.provider = provider
        self.model = os.environ.get("LLM_MODEL") or DEFAULT_MODELS[provider]
        self.effort = conf.get("effort")
        self.batch_size = conf.get("batch_size", 20)
        self.concurrency = conf.get("concurrency", 4)
        self.system = _system_prompt(cfg)
        self.curate_system = CURATE_PROMPT.format(
            reader=cfg.reader_profile or "an engineer who follows these topics")
        self.topic_ids = {t["id"] for t in cfg.topics}
        self._use_fallbacks = self.model.startswith(_FALLBACK_MODELS)
        self._client = None
        self.last_error = ""   # most recent batch failure, used for the Telegram alert
        log.info("LLM: %s / %s", self.provider, self.model)

    @staticmethod
    def _github_token() -> str | None:
        return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    # ---- providers ----
    def _call_anthropic(self, payload: str, system: str, schema: dict) -> str:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        kwargs = dict(
            model=self.model, max_tokens=16000, system=system,
            messages=[{"role": "user", "content": payload}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if self.effort:
            kwargs["output_config"]["effort"] = self.effort
        if self._use_fallbacks:
            try:
                msg = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            except (anthropic.BadRequestError, TypeError) as e:
                log.warning("fallbacks parameter not accepted, retrying without it: %s", e)
                self._use_fallbacks = False
                msg = self._client.messages.create(**kwargs)
        else:
            msg = self._client.messages.create(**kwargs)
        if msg.stop_reason == "refusal":
            raise RuntimeError("the model refused this batch")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("output was cut off by max_tokens")
        return next(b.text for b in msg.content if b.type == "text")

    def _call_chat_completions(self, payload: str, system: str) -> str:
        """OpenAI-compatible chat/completions, shared by github_models and openai_compatible."""
        import requests

        if self.provider == "github_models":
            url, key = GITHUB_MODELS_URL, self._github_token()
        else:
            base = (os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
            url, key = f"{base}/chat/completions", os.environ.get("LLM_API_KEY")
        if not key:
            raise RuntimeError("missing GITHUB_TOKEN" if self.provider == "github_models" else "missing LLM_API_KEY")
        resp = requests.post(
            url, timeout=180,
            headers={"Authorization": f"Bearer {key}", "User-Agent": http.user_agent()},
            json={"model": self.model, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": payload}]},
        )
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if "json" not in resp.headers.get("content-type", ""):
            raise RuntimeError("the endpoint did not return JSON (blocked by the network?)")
        return resp.json()["choices"][0]["message"]["content"]

    def _call(self, payload: str, system: str, schema: dict) -> dict:
        if self.provider == "anthropic":
            text = self._call_anthropic(payload, system, schema)
        else:
            text = self._call_chat_completions(payload, system)
        return json.loads(text[text.find("{"):text.rfind("}") + 1])

    # ---- scoring ----
    def _score_batch(self, batch: list[Item]) -> None:
        for row in self._call(_payload(batch), self.system, SCHEMA)["items"]:
            idx = int(row["id"])
            if not 0 <= idx < len(batch):
                continue
            it = batch[idx]
            it.relevance = max(1, min(10, int(row["relevance"])))
            it.topic = row.get("topic") if row.get("topic") in self.topic_ids else ""
            it.summary = str(row.get("summary") or "").strip()
            it.event = str(row.get("event") or "").strip()

    def _score_with_retry(self, n: int, total: int, batch: list[Item]) -> list[Item]:
        for attempt in (1, 2):
            try:
                self._score_batch(batch)
                return [it for it in batch if it.relevance]
            except Exception as e:
                self.last_error = str(e)
                log.warning("LLM batch %d/%d attempt %d failed: %s", n, total, attempt, e)
        log.error("LLM batch %d/%d skipped (%d items left unscored, retried next run)", n, total, len(batch))
        return []

    def score(self, items: list[Item]) -> list[Item]:
        """Score items in place and return the ones that were scored. A failing batch is
        retried once, then skipped and logged."""
        batches = [items[i:i + self.batch_size] for i in range(0, len(items), self.batch_size)]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            results = pool.map(lambda nb: self._score_with_retry(nb[0], len(batches), nb[1]),
                               enumerate(batches, 1))
            return [it for group in results for it in group]

    def curate(self, items: list[Item]) -> list[Item]:
        """One pass over everything that cleared the threshold: merge items reporting the same
        event and rank by value. Falls back to sorting by relevance (no merging) on failure."""
        fallback = sorted(items, key=lambda i: -i.relevance)
        if len(items) < 2:
            return fallback
        payload = json.dumps(
            [{"id": n, "type": it.type, "source": it.source, "title": it.title,
              "summary": it.summary, "relevance": it.relevance} for n, it in enumerate(items)],
            ensure_ascii=False)
        try:
            groups = self._call(payload, self.curate_system, CURATE_SCHEMA)["groups"]
        except Exception as e:
            log.warning("curation (merge + rank) failed, falling back to relevance order: %s", e)
            return fallback
        # Models sometimes group items that only share a theme. Treat a member as a duplicate
        # only if it shares real keywords with the group's first item; put the rest back.
        sig = [tokens(f"{it.title} {it.event}") for it in items]
        ordered, dropped, used = [], set(), set()
        for group in groups:
            ids = [int(g) for g in (group if isinstance(group, list) else [group])
                   if str(g).lstrip("-").isdigit() and 0 <= int(g) < len(items) and int(g) not in used]
            if not ids:
                continue
            head = ids[0]
            used.add(head)
            ordered.append(items[head])
            for other in ids[1:]:
                if len(sig[head] & sig[other]) >= 2:
                    used.add(other)
                    dropped.add(other)
        rest = sorted((n for n in range(len(items)) if n not in used), key=lambda n: -items[n].relevance)
        log.info("curation: %d items, %d duplicate reports merged", len(items), len(dropped))
        return ordered + [items[n] for n in rest]
