#!/usr/bin/env bash
# Deploy one environment on the VPS. Run by the GitHub Action over SSH (or by hand).
#   deploy/deploy.sh <live|preview> [git-sha]       (default: HEAD of the checkout)
#
# Layout on the VPS (created by deploy/bootstrap.sh):
#   /opt/source-trace/live      checkout of main      + .env
#   /opt/source-trace/preview   checkout of dev       + .env
# Steps: lock -> build + up (compose project source-trace-<env>) -> smoke test -> on failure roll back
# to the previous commit and exit 1. The database volume is kept; RESET_DB=1 wipes it first (preview only,
# for schema changes: create_all never alters existing tables).
set -euo pipefail
env_name="${1:-}"; sha="${2:-}"
case "$env_name" in live|preview) ;; *) echo "usage: $0 <live|preview> [git-sha]" >&2; exit 64 ;; esac
[[ -z "$sha" || "$sha" =~ ^[0-9a-f]{7,40}$ ]] || { echo "deploy: bad sha" >&2; exit 64 ;}
[ "${RESET_DB:-0}" != 1 ] || [ "$env_name" = preview ] || { echo "deploy: RESET_DB is preview-only" >&2; exit 64 ;}

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "deploy: $(pwd)/.env missing (see deploy/bootstrap.sh)" >&2; exit 1 ;}
exec 9>"/tmp/source-trace-deploy-$env_name.lock"
flock -w 600 9 || { echo "deploy: another deploy of $env_name is running" >&2; exit 1 ;}

export ENV_NAME="$env_name"
project="source-trace-$env_name"
compose() { docker compose -p "$project" -f deploy/docker-compose.yml -f deploy/docker-compose.vps.yml --env-file .env "$@"; }
log() { echo "deploy[$env_name] $*"; }

smoke() {  # inside the stack: no public DNS/TLS needed, tests proxy + backend + DB together
  local want="$1" i out
  for i in $(seq 30); do
    if out="$(compose exec -T web wget -qO- http://localhost/api/health 2>/dev/null)" \
       && [[ "$out" == *'"ok"'*true* ]]; then
      [[ "$out" == *"${want:0:7}"* ]] || { log "health answers, but not with version $want: $out"; return 1 ;}
      compose exec -T web wget -qO- http://localhost/api/health/db >/dev/null 2>&1 || { log "db health failed"; return 1 ;}
      compose exec -T web wget -qO- http://localhost/ >/dev/null 2>&1 || { log "frontend not served"; return 1 ;}
      # demo cache must answer once B9 exists; 404 before that is tolerated unless SMOKE_DEMO=1
      if ! compose exec -T web wget -qO- http://localhost/api/demo >/dev/null 2>&1; then
        [ "${SMOKE_DEMO:-0}" != 1 ] || { log "/api/demo failed"; return 1 ;}
        log "warning: /api/demo not available yet"
      fi
      return 0
    fi
    sleep 4
  done
  log "no healthy /api/health after 120 s"; return 1
}

up() {  # up <sha>
  git checkout -q --detach "$1"
  export GIT_SHA; GIT_SHA="$(git rev-parse --short HEAD)"
  [ "${RESET_DB:-0}" != 1 ] || { log "RESET_DB: removing volumes"; compose down -v --remove-orphans; }
  compose up -d --build --remove-orphans
}

previous="$(git rev-parse HEAD)"
git fetch -q origin
target="${sha:-$(git rev-parse HEAD)}"
log "deploying $target (previous $previous)"
up "$target"
if smoke "$target"; then
  log "OK $(git rev-parse --short HEAD)"; docker image prune -f >/dev/null 2>&1 || true; exit 0
fi
log "FAILED, rolling back to $previous"; compose logs --tail 40 backend web || true
RESET_DB=0 up "$previous"
smoke "$previous" && log "rolled back OK" || log "ROLLBACK ALSO UNHEALTHY, needs a human"
exit 1
