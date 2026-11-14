#!/usr/bin/env bash
# ONE-TIME setup on the VPS, run by a human (needs docker + compose >= 2.24, git):
#   LIVE_HOST=trace.example.com PREVIEW_HOST=preview.trace.example.com deploy/bootstrap.sh [repo-url]
# Safe to re-run: never overwrites an existing .env.
set -euo pipefail
: "${LIVE_HOST:?set LIVE_HOST}"; : "${PREVIEW_HOST:?set PREVIEW_HOST}"
repo="${1:-https://github.com/hotreload-junction/Source-Trace.git}"   # private repo: use a read-only deploy-key URL
root="${DEPLOY_ROOT:-/opt/source-trace}"
mkdir -p "$root"
for pair in live:main preview:dev; do
  env_name="${pair%%:*}"; branch="${pair##*:}"; dir="$root/$env_name"
  [ -d "$dir/.git" ] || git clone -q --branch "$branch" "$repo" "$dir"
  if [ ! -f "$dir/.env" ]; then
    cp "$dir/.env.example" "$dir/.env"
    printf '\n# --- VPS (%s) ---\nMARIADB_PASSWORD=%s\nPUBLIC_BASE_URL=https://%s\n' \
      "$env_name" "$(openssl rand -hex 16)" "$([ "$env_name" = live ] && echo "$LIVE_HOST" || echo "$PREVIEW_HOST")" >> "$dir/.env"
    echo "bootstrap: wrote $dir/.env, now add the API keys (AI_ROUTER_*, CONTEXT_DEV_*, EXA_*) by hand"
  fi
done
docker network inspect edge >/dev/null 2>&1 || docker network create edge
printf 'LIVE_HOST=%s\nPREVIEW_HOST=%s\n' "$LIVE_HOST" "$PREVIEW_HOST" > "$root/live/deploy/edge/.env"
( cd "$root/live/deploy/edge" && docker compose up -d )
echo "bootstrap: edge proxy is up. DNS for $LIVE_HOST and $PREVIEW_HOST must point at this VPS."
echo "bootstrap: stop the standalone MariaDB only if it should not keep running; the stacks use their own db."
