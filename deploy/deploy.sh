#!/usr/bin/env bash
# Deploy one release of one environment on the VPS. The GitHub Action uploads the release (git archive, no git or
# repo access needed on the VPS) and runs this from inside it:
#   /opt/source-trace/<env>/releases/<sha>/deploy/deploy.sh <live|preview> <sha>
#
# Layout (made by deploy/bootstrap.sh):
#   /opt/source-trace/<env>/.env            secrets + SITE_HOST, never in git
#   /opt/source-trace/<env>/releases/<sha>  one directory per deployed commit (last KEEP_RELEASES kept)
#   /opt/source-trace/<env>/current         symlink to the release that is running
# Steps: lock -> build + up (compose project source-trace-<env>) -> smoke test -> on failure roll back to the
# release `current` pointed to before, exit 1. The DB volume is kept; RESET_DB=1 wipes it first (preview only,
# for schema changes: create_all never alters existing tables).
set -euo pipefail
env_name="${1:-}"; sha="${2:-}"
case "$env_name" in live|preview) ;; *) echo "usage: $0 <live|preview> <git-sha>" >&2; exit 64 ;; esac
[[ "$sha" =~ ^[0-9a-f]{7,40}$ ]] || { echo "deploy: bad or missing sha" >&2; exit 64; }
[ "${RESET_DB:-0}" != 1 ] || [ "$env_name" = preview ] || { echo "deploy: RESET_DB is preview-only" >&2; exit 64; }

release="$(cd "$(dirname "$0")/.." && pwd)"
envdir="$(dirname "$(dirname "$release")")"            # .../<env>
[ "$(basename "$release")" = "$sha" ] || { echo "deploy: $release is not the release directory of $sha" >&2; exit 64; }
[ -f "$envdir/.env" ] || { echo "deploy: $envdir/.env missing (see deploy/bootstrap.sh)" >&2; exit 1; }
exec 9>"$envdir/.deploy.lock"
flock -w 600 9 || { echo "deploy: another deploy of $env_name is running" >&2; exit 1; }

export ENV_NAME="$env_name"
project="source-trace-$env_name"
log() { echo "deploy[$env_name] $*"; }
compose() {  # compose <release-dir> <args...>
  local dir="$1"; shift
  ( cd "$dir" && docker compose -p "$project" -f deploy/docker-compose.yml -f deploy/docker-compose.vps.yml \
      --env-file "$envdir/.env" "$@" )
}

smoke() {  # smoke <release-dir> <sha>: inside the stack, so it needs no DNS/TLS and tests proxy + backend + DB together
  local dir="$1" want="$2" i out
  for i in $(seq 30); do
    if out="$(compose "$dir" exec -T web wget -qO- http://localhost/api/health 2>/dev/null)" \
       && [[ "$out" == *'"ok"'*true* ]]; then
      [[ "$out" == *"${want:0:7}"* ]] || { log "health answers, but not with version $want: $out"; return 1; }
      compose "$dir" exec -T web wget -qO- http://localhost/api/health/db >/dev/null 2>&1 || { log "db health failed"; return 1; }
      compose "$dir" exec -T web wget -qO- http://localhost/ >/dev/null 2>&1 || { log "frontend not served"; return 1; }
      # demo cache must answer once B9 exists; unavailable before that is tolerated unless SMOKE_DEMO=1
      if ! compose "$dir" exec -T web wget -qO- http://localhost/api/demo >/dev/null 2>&1; then
        [ "${SMOKE_DEMO:-0}" != 1 ] || { log "/api/demo failed"; return 1; }
        log "warning: /api/demo not available yet"
      fi
      return 0
    fi
    sleep 4
  done
  log "no healthy /api/health after 120 s"; return 1
}

up() {  # up <release-dir> <sha>
  local dir="$1"
  ln -sfn "$envdir/.env" "$dir/.env"                    # base compose reads ../.env relative to deploy/
  export GIT_SHA="${2:0:7}"
  [ "${RESET_DB:-0}" != 1 ] || { log "RESET_DB: removing volumes"; compose "$dir" down -v --remove-orphans; }
  compose "$dir" up -d --build --remove-orphans
}

previous=""; [ -L "$envdir/current" ] && previous="$(readlink -f "$envdir/current")"
log "deploying $sha (previous: ${previous:-none})"
if up "$release" "$sha" && smoke "$release" "$sha"; then
  ln -sfn "$release" "$envdir/current"
  # keep the newest KEEP_RELEASES releases (never current)
  ls -1dt "$envdir"/releases/*/ | tail -n +$(( ${KEEP_RELEASES:-4} + 1 )) | while read -r old; do
    [ "$(readlink -f "$old")" = "$(readlink -f "$envdir/current")" ] || rm -rf "$old"
  done
  docker image prune -f >/dev/null 2>&1 || true
  log "OK $sha"; exit 0
fi
log "FAILED"; compose "$release" logs --tail 40 backend web || true
if [ -n "$previous" ] && [ -d "$previous" ]; then
  log "rolling back to $previous"
  prev_sha="$(basename "$previous")"
  RESET_DB=0 up "$previous" "$prev_sha" && smoke "$previous" "$prev_sha" && log "rolled back OK" \
    || log "ROLLBACK ALSO UNHEALTHY, needs a human"
else
  log "no previous release to roll back to"
fi
exit 1
