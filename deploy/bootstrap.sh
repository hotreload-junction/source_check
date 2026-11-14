#!/usr/bin/env bash
# ONE-TIME setup on the VPS, run by a human (needs docker + compose >= 2.24, git):
#   LIVE_HOST=source-trace.example.com PREVIEW_HOST=source-trace-preview.example.com deploy/bootstrap.sh [repo-url]
# Needs the VPS's Traefik (docker provider, certresolver `letsencrypt`) and read access to the repo (deploy key
# for a private repo: pass an ssh URL). Safe to re-run: never overwrites an existing .env.
set -euo pipefail
: "${LIVE_HOST:?set LIVE_HOST}"; : "${PREVIEW_HOST:?set PREVIEW_HOST}"
repo="${1:-https://github.com/hotreload-junction/Source-Trace.git}"   # private repo: use a read-only deploy-key URL
root="${DEPLOY_ROOT:-/opt/source-trace}"
mkdir -p "$root"
for pair in live:main preview:dev; do
  env_name="${pair%%:*}"; branch="${pair##*:}"; dir="$root/$env_name"
  [ -d "$dir/.git" ] || git clone -q --branch "$branch" "$repo" "$dir"
  host="$([ "$env_name" = live ] && echo "$LIVE_HOST" || echo "$PREVIEW_HOST")"
  if [ ! -f "$dir/.env" ]; then
    cp "$dir/.env.example" "$dir/.env"
    printf '\n# --- VPS (%s) ---\nMARIADB_PASSWORD=%s\nSITE_HOST=%s\nPUBLIC_BASE_URL=https://%s\n' \
      "$env_name" "$(openssl rand -hex 16)" "$host" "$host" >> "$dir/.env"
    echo "bootstrap: wrote $dir/.env, now add the API keys (AI_ROUTER_*, CONTEXT_DEV_*, EXA_*) by hand"
  fi
done
echo "bootstrap: done. DNS for $LIVE_HOST and $PREVIEW_HOST must resolve to this VPS (Traefik issues the certificates)."
