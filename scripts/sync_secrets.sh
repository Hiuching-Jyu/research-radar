#!/usr/bin/env bash
# Sync the local .env and private config to the GitHub repo's Secrets / Variables.
# Usage: bash scripts/sync_secrets.sh   (needs a logged-in gh CLI)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

printf '%s' "$TELEGRAM_BOT_TOKEN" | gh secret set TELEGRAM_BOT_TOKEN
printf '%s' "$TELEGRAM_CHAT_ID"   | gh secret set TELEGRAM_CHAT_ID
[ -n "${LLM_API_KEY:-}" ]       && printf '%s' "$LLM_API_KEY"       | gh secret set LLM_API_KEY
[ -n "${ANTHROPIC_API_KEY:-}" ] && printf '%s' "$ANTHROPIC_API_KEY" | gh secret set ANTHROPIC_API_KEY
gh secret set TOPICS_YAML < config/topics.yaml
gh secret set FEEDS_YAML  < config/feeds.yaml

[ -n "${LLM_PROVIDER:-}" ] && gh variable set LLM_PROVIDER -b "$LLM_PROVIDER"
[ -n "${LLM_BASE_URL:-}" ] && gh variable set LLM_BASE_URL -b "$LLM_BASE_URL"
[ -n "${LLM_MODEL:-}" ]    && gh variable set LLM_MODEL    -b "$LLM_MODEL"
echo "Synced:"; gh secret list; gh variable list
