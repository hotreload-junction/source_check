# Hermes as the third (cloud) teammate

Hermes runs 24/7 on a small cloud VM, has its own worktree and handle (`hermes`), and talks to the
agents through the same board. It also talks to the humans on Telegram/Discord, so you can steer it
from your phone while walking around the venue.

## What it does (pick 2-3, don't give it everything)

| Role | Why a cloud agent is good at it | Trigger |
|---|---|---|
| **QA / integrator on `dev`** | Pulls every new `dev`, runs the app + an e2e smoke test of the demo flow in a real browser, posts BLOCKED/BREAKING with repro steps to the owner. Catches "both halves work, together they don't". | board: every DONE (`wait --any`) or cron 20 min |
| **Digest / coordinator** | Reads the board + `git log dev`, sends the humans a 10-line status: shipped, blocked, unanswered questions, idle agents, drift from PLAN.md. | cron every 45 min -> Telegram |
| **Research & spikes** | Partner API docs, datasets, "can model X do Y in < 2 s?" Answers QUESTIONs addressed to `hermes` with sources. | board mention |
| **Night shift** | Keeps working PLAN.md tasks (tests, seed data, docs, polish) while the humans sleep and laptops are closed. | `tools/autopilot` + `--task` |
| **Pitch & submission** | Drafts README, devpost/submission text, slide outline and demo script from PLAN/DECISIONS/git log; records demo screenshots. | Sat evening, on request |

Recommended for 2 humans: **QA/integrator + digest + night shift**.

## Setup (≈ 30 min, do it before Friday)

On a VM (Hetzner/Fly/any Ubuntu box, 2 vCPU/4 GB is enough; Docker if the app needs it):

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup                       # model + provider
# GitHub access: fine-grained PAT limited to THIS repo (contents rw, issues rw), stored by gh:
gh auth login --with-token < pat.txt
git clone https://github.com/<you>/<repo>.git ~/hack && cd ~/hack
tools/worktree hermes              # -> ~/hack-hermes on agent/hermes, handle "hermes"
export BOARD_ISSUE=<n>             # if the board is not #1
```

Messaging to the humans: `hermes gateway setup` (Telegram is quickest), then run the gateway as a
service so cron deliveries and your phone messages reach it.

### 1) Board-driven work (questions, BREAKINGs, night shift)

```bash
cd ~/hack-hermes
tmux new -s hermes-board \
  'tools/autopilot --max-per-hour 10 --task "Own QA of dev (see docs/hermes-teammate.md) and the hermes tasks in PLAN.md" \
     -- hermes chat -Q --yolo -q'
```
`--yolo` skips Hermes' command approvals; acceptable on a throwaway VM that only holds this repo and a
repo-scoped token. Don't put other credentials on that box.

### 2) QA on every ship

Run a second loop that wakes on *any* post and only acts on DONEs (cheap filter before the LLM):
```bash
tmux new -s hermes-qa 'while :; do
  p="$(tools/board wait --any --interval 45)" || continue
  echo "$p" | grep -q " DONE" || continue
  hermes chat -Q --yolo -q "New ships on dev:
$p
Pull origin/dev, run tools/check and the e2e smoke test of the demo flow (BRIEF.md/PLAN.md). If something
is broken, post BLOCKED --to <the shipper> with exact repro steps; fix it yourself if it is tiny and in e2e/ or docs/.
If all is fine, post nothing."
done'
```
Alternative without polling: a GitHub webhook (`issue_comment` + `push`) to `hermes webhook subscribe`
(needs the VM reachable over HTTPS, e.g. cloudflared). Polling is simpler and good enough at 30-45 s.

### 3) Digest for the humans (cron)

In a Hermes session on the VM:
> Every 45 minutes from Fri 20:00 to Sun 10:00: in ~/hack-hermes run `tools/board read -n 60`,
> `tools/board roster` and `git fetch && git log --oneline origin/main..origin/dev`. Send me a digest of
> max 10 lines on Telegram: shipped since last digest, blocked/unanswered questions (who waits on whom),
> agents silent > 60 min, whether dev is green and worth promoting, and drift from PLAN.md.
> Send nothing if nothing changed.

(Hermes cron runs are capped at a few minutes each, so keep the cron job to reading and summarising;
long work belongs in the autopilot loops above.)

## Rules for Hermes (in addition to AGENTS.md)

- Handle `hermes`, owns `e2e/` and `docs/`; may fix small bugs anywhere, ships via `tools/ship` like everyone.
- Never pushes to `main`, never holds credentials beyond the repo-scoped token.
- Reports problems to the *owner* of the broken part, not to `all`.
- Digest goes to the humans via Telegram, **not** to the board (keeps the board signal-only).
