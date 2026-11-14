# Deployment: live (main) + preview (dev)

```
tools/ship -> dev --push--> Action: tools/check -> SSH -> deploy.sh preview   https://<PREVIEW_HOST>
tools/promote -> main --push--> Action: tools/check -> SSH -> deploy.sh live  https://<LIVE_HOST>
```
Agents still never push to `main`; only a human's `tools/promote` moves it, so live changes only on purpose.

## What runs on the VPS
- `/opt/source-trace/live` (branch main) and `/opt/source-trace/preview` (branch dev): a checkout plus a `.env` each.
- Two compose projects `source-trace-live` / `source-trace-preview` (own MariaDB volume each, no host ports).
- One shared edge Caddy (`deploy/edge/`) on 80/443 routes by hostname and gets the certificates.
- `deploy/deploy.sh <env> <sha>`: build + up, smoke test inside the stack (`/api/health` with the new version,
  `/api/health/db`, frontend, `/api/demo` warning until B9), **automatic rollback** to the previous commit on failure
  (exit 1 -> Action red -> `[ci] NOTE` on the board). Deploys of one env never overlap (flock + Action concurrency).
- The script that runs is the one already on the VPS, so a change to `deploy.sh` takes effect one deploy later.

## One-time setup (humans: needs secrets and DNS)
1. DNS: `LIVE_HOST` and `PREVIEW_HOST` A records -> the VPS.
2. On the VPS (docker compose >= 2.24, git, openssl):
   `git clone <repo> /tmp/st && LIVE_HOST=... PREVIEW_HOST=... /tmp/st/deploy/bootstrap.sh`
   then add the API keys to `/opt/source-trace/{live,preview}/.env` (AI Router, Context.dev, Exa).
3. A deploy user in the `docker` group; its `~/.ssh/authorized_keys` gets a NEW key pair made only for this
   (`ssh-keygen -t ed25519 -f deploy_key -N ""`). Tip: restrict it with `restrict,command=...` if you want.
4. GitHub repo settings: secrets `DEPLOY_SSH_KEY` (private key), `DEPLOY_HOST_KEY` (`ssh-keyscan -t ed25519 <host>`
   line); variables `DEPLOY_HOST`, optional `DEPLOY_USER` (default `deploy`), `DEPLOY_ROOT`.
   The workflow does nothing until `DEPLOY_HOST` is set.
5. Optional: GitHub environment `live` with a required reviewer for a manual gate.

## Operations
- Manual deploy: `ssh deploy@host 'cd /opt/source-trace/preview && git fetch -q && deploy/deploy.sh preview <sha>'`.
- Schema changed (create_all never alters tables, e.g. B8's `source_url`): `RESET_DB=1 deploy/deploy.sh preview <sha>` (preview only).
- The standalone `mariadb-hackathon` project is untouched; the stacks use their own db and publish no host ports.
- Preview and live share the same API keys unless the `.env` files differ: mind the rate limits (Context.dev 10 req/min).
