# Deployment: live (main) + preview (dev)

```
tools/ship    -> dev  --push--> Action: tools/check -> SSH -> deploy.sh preview   https://preview.sourcecheck.tech
tools/promote -> main --push--> Action: tools/check -> SSH -> deploy.sh live      https://sourcecheck.tech
```
Agents still never push to `main`; only a human's `tools/promote` moves it, so live changes only on purpose.

## What runs on the VPS (72.61.22.175, Hostinger)
- `/opt/source-trace/{live,preview}/`: `.env` (secrets, `SITE_HOST`), `releases/<sha>/` (last 4 kept) and `current` -> running release.
  No git and no repo access on the VPS (the repo is private, deploy keys are disabled): the Action uploads `git archive` of the commit.
- Two compose projects `source-trace-live` / `source-trace-preview`, own MariaDB volume each, **no host ports**.
- The VPS's existing **Traefik** (`/docker/traefik`, also serves the Hermes agent) routes by hostname via the labels in
  `deploy/docker-compose.vps.yml` and issues the Let's Encrypt certificates. Do not run another proxy on 80/443.
- The standalone `mariadb-hackathon` container (127.0.0.1:3306) is untouched.
- `deploy/deploy.sh <env> <sha>`: build + up, smoke test inside the stack (`/api/health` with the new version,
  `/api/health/db`, frontend, `/api/demo` warning until B9), **automatic rollback** to the previous commit on failure
  (exit 1 -> Action red -> `[ci] NOTE` on the board). Deploys of one env never overlap (flock + Action concurrency).
- The `deploy.sh` that runs is always the one from the commit being deployed.
- DNS (Hostinger, domain sourcecheck.tech): `@` and `preview` A -> VPS, `www` CNAME -> `@`. Pre-change snapshot 196964788
  (the old second A record `2.57.91.91` was only Hostinger's parking page).

## Setup (done once; `deploy/bootstrap.sh` + steps below)
1. DNS records as above.
2. VPS user `deploy` (docker group). The Action logs in with its own ed25519 key. API keys live only in
   `/opt/source-trace/{live,preview}/.env`.
3. Upload a release and run `deploy/bootstrap.sh` once (`LIVE_HOST=sourcecheck.tech PREVIEW_HOST=preview.sourcecheck.tech`).
4. GitHub: secrets `DEPLOY_SSH_KEY`, `DEPLOY_HOST_KEY`; variables `DEPLOY_HOST`, `DEPLOY_USER`. The workflow does nothing
   until `DEPLOY_HOST` is set. Optional: GitHub environment `live` with a required reviewer.

## Operations
- Manual deploy: `re-run the workflow (Actions -> deploy -> Run workflow), or on the VPS `/opt/source-trace/preview/releases/<sha>/deploy/deploy.sh preview <sha>` to redeploy an uploaded release`.
- Schema changed (create_all never alters tables, e.g. B8's `source_url`): `RESET_DB=1 .../releases/<sha>/deploy/deploy.sh preview <sha>` (preview only).
- Preview and live share the same API keys unless the `.env` files differ: mind the rate limits (Context.dev 10 req/min).
