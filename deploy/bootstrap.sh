#!/usr/bin/env bash
# ONE-TIME setup on the VPS (as the deploy user; needs docker + compose >= 2.24, openssl). Run from an uploaded release:
#   git archive HEAD | ssh deploy@host 'mkdir -p /tmp/st && tar -x -C /tmp/st'
#   ssh deploy@host 'LIVE_HOST=... PREVIEW_HOST=... /tmp/st/deploy/bootstrap.sh'
# Creates /opt/source-trace/{live,preview}/{releases,.env}. Never overwrites an existing .env.
# Routing + certificates come from the VPS's Traefik (labels in docker-compose.vps.yml): nothing to start here.
set -euo pipefail
: "${LIVE_HOST:?set LIVE_HOST}"; : "${PREVIEW_HOST:?set PREVIEW_HOST}"
src="$(cd "$(dirname "$0")/.." && pwd)"
root="${DEPLOY_ROOT:-/opt/source-trace}"
for env_name in live preview; do
  dir="$root/$env_name"; mkdir -p "$dir/releases"
  host="$([ "$env_name" = live ] && echo "$LIVE_HOST" || echo "$PREVIEW_HOST")"
  if [ ! -f "$dir/.env" ]; then
    # PROMPT_VERSION is never pinned on the VPS: the default in backend/app/config.py is the truth (Leo, 14 Nov)
    grep -v '^PROMPT_VERSION=' "$src/.env.example" > "$dir/.env"; chmod 600 "$dir/.env"
    printf '\n# --- VPS (%s) ---\nMARIADB_PASSWORD=%s\nSITE_HOST=%s\nPUBLIC_BASE_URL=https://%s\n' \
      "$env_name" "$(openssl rand -hex 16)" "$host" "$host" >> "$dir/.env"
    echo "bootstrap: wrote $dir/.env (add the API keys: AI_ROUTER_*, CONTEXT_DEV_*, EXA_*)"
  fi
done
echo "bootstrap: done. DNS for $LIVE_HOST and $PREVIEW_HOST must resolve to this VPS."
