# Research Radar

A small bot that collects **new papers, products, open-source projects and industry news** on the
topics you care about, lets an LLM throw out the noise and write a one-line summary for what is
left, and pushes the result to Telegram. It searches by topic across the web rather than watching
a fixed list of companies, and it runs entirely on GitHub Actions.

## How it works

```
fetch sources -> normalize -> time filter -> dedupe -> LLM scoring -> threshold + merge same-event items -> push to Telegram -> update data/sent.json
```

| Source | Module | Notes |
|---|---|---|
| arXiv | `radar/sources/arxiv_src.py` | One query per topic: `arxiv_categories` AND `arxiv_keywords` |
| Google News | `radar/sources/google_news.py` | One search feed per `queries_en` / `queries_zh` entry (English and Chinese editions) |
| RSS/Atom feeds | `radar/sources/rss.py` | Media, company blogs and tracked repos from `config/feeds.yaml` |
| Hacker News | `radar/sources/hackernews.py` | Algolia search, title matches only, 7-day window |
| GitHub | `radar/sources/github_search.py` | Repos created in the last 7 days with at least 5 stars |

Each push contains 10 top picks (with a summary) followed by up to 20 more items (title and link only).

## Private config

What you follow is personal, so it is **not committed**:

| What | In the repo | Actually used |
|---|---|---|
| Topics, keywords, reader profile | `config/topics.example.yaml` | `config/topics.yaml` locally; the `TOPICS_YAML` secret on Actions |
| Feeds and tracked repos | `config/feeds.example.yaml` | `config/feeds.yaml` locally; the `FEEDS_YAML` secret on Actions |

After editing either file locally, sync it (together with the keys in `.env`) to the repo:

```bash
bash scripts/sync_secrets.sh
```

`data/sent.json` stores only salted hashes (the salt is the bot token, or `STATE_SALT` if set), so it
does not reveal what was pushed. Actions logs never contain queries or item titles.

## Setup

### 1. Create a Telegram bot

1. In Telegram, open **@BotFather**, send `/newbot` and follow the prompts. You get a **token** that
   looks like `123456:ABC-DEF...`.
2. Open a chat with your new bot and press **Start** (or send it any message). A bot cannot message
   someone who has never talked to it.
3. Open `https://api.telegram.org/bot<token>/getUpdates` in a browser and find
   `"chat":{"id":123456789,...}`. That number is your **chat id**. If the result is empty, send the
   bot another message and reload. To push to a group, add the bot to the group, post a message
   there and look again; group ids are negative.

### 2. Configure

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                               # fill in the keys; .env is git-ignored
cp config/topics.example.yaml config/topics.yaml   # your topics
cp config/feeds.example.yaml config/feeds.yaml     # your feeds
```

### 3. Repository secrets and variables

`bash scripts/sync_secrets.sh` sets all of these from `.env` and `config/`. To do it by hand, go to
Settings -> Secrets and variables -> Actions.

Secrets:

| Name | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token |
| `TELEGRAM_CHAT_ID` | yes | Chat that receives the pushes |
| `TOPICS_YAML` | yes | Full content of `config/topics.yaml` |
| `FEEDS_YAML` | yes | Full content of `config/feeds.yaml` |
| `LLM_API_KEY` | for OpenAI-compatible services | API key of that service |
| `ANTHROPIC_API_KEY` | for Anthropic | Anthropic API key |

`GITHUB_TOKEN` is provided by Actions automatically.

Variables (optional):

| Name | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `github_models` or `openai_compatible` |
| `LLM_MODEL` | depends on the provider: `claude-opus-5-5` / `openai/gpt-4o-mini` / `deepseek-chat` | Model name |
| `LLM_BASE_URL` | `https://api.deepseek.com` | `openai_compatible` only: base URL of the service |

Three ways to get an LLM: any OpenAI-compatible service, Anthropic, or GitHub Models (free tier, no
extra key needed).

### 4. Run

- Scheduled (`.github/workflows/radar.yml`): twice a day. The cron lines are in UTC; edit them to
  change the times. Scheduled runs on GitHub often start several minutes late.
- Manual: Actions -> Research Radar -> Run workflow, with an optional `mode` (all / papers / news).
- GitHub pauses schedules after 60 days without repo activity. Every push commits `sent.json`, so
  this normally does not happen.

## Running locally

```bash
.venv/bin/python -m radar.main --mode all --dry-run             # print only; nothing sent, no state written
.venv/bin/python -m radar.main --mode all --dry-run --show-all  # also list items below the threshold with scores
.venv/bin/python -m radar.main --dry-run --skip-llm             # per-source counts and failures only
.venv/bin/python -m radar.main --mode news --sources rss        # only the given sources
```

Dry runs print item titles, which is why the workflow does not offer one: Actions logs are public
on a public repo.

## Tuning

### Topics

Edit `config/topics.yaml` (format in `config/topics.example.yaml`). Each topic has:

```yaml
- id: solid_state_battery            # unique id
  name: Solid-state batteries        # shown in the push
  description: >                     # used by the LLM: say what counts and what does not
    ...
  queries_en: ['"solid-state battery"']   # Google News (English) + Hacker News
  queries_zh: []                          # Google News (Chinese), optional
  arxiv_keywords: [solid-state battery]   # arXiv; the first 5 are also used for GitHub search
```

The top of the file also holds `reader_profile`, `scoring_notes`, `arxiv_categories`,
`summary_language`, `utc_offset_hours` and optional `labels` for the display strings.

Removing a topic means deleting its block; no code changes. When the results are off, the most
effective fix is usually the `description` (for example, add a sentence about what does not count).
Run `bash scripts/sync_secrets.sh` afterwards.

### Threshold and other settings

`config/settings.yaml`:

- `relevance_threshold`: items scoring below this never appear in a push (default 7).
- `digest.featured` / `digest.more`: number of top picks (with summary) and of extra items (title only) per push, default 10 and 20.
- `notify_when_empty`: send a "nothing new today" message when there is nothing to push.
- `lookback_hours`: time window per item type.
- `dedup.title_similarity` / `dedup.merge_similarity`: title dedupe threshold / threshold for merging same-event items after filtering.
- `llm.batch_size`, `llm.concurrency`, `llm.max_candidates`, `llm.effort`.

### Adding sources

- **RSS/Atom feed**: add a `name` + `url` entry under `media` or `company_blogs` in `config/feeds.yaml`.
- **Track a repo or folder**: add a GitHub commits feed under `tracked_repos`, in the form
  `https://github.com/<owner>/<repo>/commits/<branch>/<path>.atom` (use `.../releases.atom` for
  releases). These items ignore the threshold: any update is pushed.
- **A new kind of source**: create a module in `radar/sources/` that implements
  `fetch(cfg) -> (list[Item], failed_request_count)` and register it in `SOURCES` in
  `radar/sources/__init__.py`.

Run `bash scripts/sync_secrets.sh` after changing `config/feeds.yaml`.

## Behavior

- **Dedupe**: in `data/sent.json`, `sent` holds pushed items and `seen` holds items that were already
  scored (so they are not paid for twice). Both keep 30 days of salted hashes. Google News links are
  redirect links, so dedupe uses URL plus title similarity. After filtering, one more LLM call merges
  reports of the same event and keeps the source that ranks highest in `source_priority`.
- **Failures**: a failing source or query is logged and skipped. If the LLM returns invalid JSON the
  batch is retried once and then skipped (it is scored on the next run). If every batch fails, the
  run exits non-zero so it shows up as failed in Actions.
- **Secrets**: read only from environment variables / `.env` / GitHub Secrets.

## Known limits

- Google News links are Google redirect links; you reach the article after a click.
- GitHub search only finds newly created repos, not new releases of older ones. To follow releases
  of a specific repo, add it to `tracked_repos`.
