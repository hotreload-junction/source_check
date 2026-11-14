# Kickoff: getting the agents running (Sat ~12:00)

Takes ~10 min. Do it in this order; the agents need `dev`, the board issue and a worktree each.

## 1. leo: once per repo
```bash
cd Source-Trace
git add -A && git commit -m "CiteTrace agent setup: brief, plan, decisions, contract v1"
BOARD_ROLE=human git push origin main      # humans only
tools/setup                                # creates dev, pinned board issue, pre-push guard
```
If the board issue is not #1: everyone `export BOARD_ISSUE=<n>` (shell rc).

## 2. both: review contract v1 together (5 min)
Read `contracts/api.md` side by side. Anything you change now is free; after the split it goes through
AGENTS.md section 4. Then mark T1 done in PLAN.md.

## 3. each human: worktrees + agents
Each agent gets its own worktree next to the repo (`../Source-Trace-<handle>`):

```bash
# leo
tools/worktree leo-be   && (cd ../Source-Trace-leo-be && claude)     # interactive, pipeline B1-B4, B7
tools/worktree leo-bg                                                  # background, signals B5-B6
cd ../Source-Trace-leo-bg && tmux new -s leo-bg \
  'tools/autopilot --task "Tasks B5 and B6 in PLAN.md (owner leo-bg)" -- claude -p --dangerously-skip-permissions'

# louis
tools/worktree louis-fe && (cd ../Source-Trace-louis-fe && claude)   # frontend F1-F5 against contracts/examples
tools/worktree louis-bg
cd ../Source-Trace-louis-bg && tmux new -s louis-bg \
  'tools/autopilot --task "Task D1 in PLAN.md, then E4 once eval/pairs.jsonl exists (owner louis-bg)" -- claude -p --dangerously-skip-permissions'
```
Codex instead of Claude for a background agent: `-- codex exec --full-auto`.
`--dangerously-skip-permissions` only on a machine/worktree without other credentials than `.env`.

Copy `.env.example` to `.env` in every worktree that runs real calls (leo-be, louis-bg) and fill in keys.

## 4. humans: steering
- Post to agents: `BOARD_AGENT=leo tools/board post NOTE --to all "..."` (or type in the issue).
- Promote when dev is green and demoable: `BOARD_ROLE=human tools/promote "what's in it"`, then deploy (D2).
- Check every ~45 min: `tools/board read -n 40`, `git log --oneline origin/main..origin/dev`.
- Code freeze Sun 08:00: tell the agents with a NOTE `--to all`, stop the autopilot tmux sessions.
