# Team rules for every agent

We are 2 humans + 4 coding agents building **CiteTrace** for Junction 2026 (Hostinger challenge).
Agents work **autonomously**: pick work, build, ship to `dev`, coordinate with other agents on the
board. Humans steer scope and promote `dev` -> `main`. Read this whole file before your first action,
then `BRIEF.md` and `PLAN.md`. Section 7 holds the CiteTrace rules that make or break the project.

## 0. Team

| Handle | Who / what | Owns (folders) | Current focus |
|---|---|---|---|
| `leo` | human, team lead + pipeline (decides scope, promotes to main) | | detection logic, prompts, model choice (Q21) |
| `louis` | human, frontend + deploy + evaluation data, demo + pitch owner | | labels eval data, picks demo cases |
| `leo-be` | Leo's Claude Code | `backend/` (except `backend/app/signals/`) | pipeline depth 1: scrape -> claims -> sources -> verify |
| `leo-bg` | Leo's background agent | `backend/app/signals/`, `backend/tests/fixtures/` | infra signals (analytics/AdSense IDs, RDAP), later depth 2 + circles |
| `louis-fe` | Louis' Claude Code | `frontend/` | input page, result view (article + Cytoscape graph), methodology page |
| `louis-bg` | Louis' background agent | `deploy/`, `eval/`, `docs/` | Hostinger VPS deploy, eval harness + metrics |
| `ci` | GitHub Actions, posts when dev goes red | | |

Shared, owned by everyone (change with care, see §4): `contracts/` (API schema/types), `.env.example`, `tools/`.
Your handle is in `.board/agent` (or `$BOARD_AGENT`). Check with `tools/board whoami`.

## 1. Sources of truth

- `BRIEF.md`: challenge text, judging criteria, partner APIs. Optimise for these.
- `PLAN.md`: demo flow and task list. Claim a task by writing your handle next to it, ship that
  edit early so others see it. Mark it done when shipped.
- `DECISIONS.md`: one line per decision. Append, never relitigate. If you disagree, post a QUESTION to the lead.
- `contracts/`: the interface between parts. Code against it, not against someone's implementation.

## 2. The board (pinned GitHub issue) - the only channel between agents

`tools/board post <TYPE> [--to a,b] "text"` adds the header `[your-handle] TYPE -> to:` automatically.

| TYPE | When | `--to` |
|---|---|---|
| `START` | you begin a task (task id from PLAN.md + folders you will touch) | optional |
| `DONE` | posted automatically by `tools/ship` | optional |
| `BREAKING` | a contract/schema/env/route change others depend on (`tools/ship --breaking`) | defaults to all |
| `BLOCKED` | you cannot continue without someone | required |
| `QUESTION` | one concrete question, answerable in one sentence | required |
| `ANSWER` | reply to a QUESTION/BLOCKED | required (the asker) |
| `NOTE` | anything else worth knowing; humans mostly | optional |

`--to` takes a handle (`louis-fe`), a human (`louis` = Louis + all `louis-*` agents) or `all`.
Write `@` names only via `--to`; plain `@` in text is defused so real GitHub users are never pinged.

Rules:
- Posts **for you** (addressed to you, your human, `all`, or any BREAKING) must be handled before you
  continue: answer, adapt, or fix. Everything else: skim the one-liners; react only if it affects your work.
- Never post just to acknowledge, thank, or narrate. No post = fine. Max ~3 lines per post; link files/commits.
- **Verify, don't trust**: before acting on someone's DONE/BREAKING, check the code on `origin/dev`.
- Staying in the loop: Claude Code gets unread posts via hooks and keeps one background `tools/board wait`.
  Other agents: `tools/board unread` before each task and before each `tools/ship`.
  Headless agents run under `tools/autopilot`, which wakes them on posts for them.

## 3. Git flow (autonomous)

```
agent/<handle> --tools/ship--> dev --(humans: tools/promote)--> main
```
- You work in your own worktree on `agent/<handle>` (created with `tools/worktree`). Never edit another worktree.
- Small commits, only your own files (no `git add -A` of unrelated files). Ship often: every working
  increment, at least every ~45 min. Small ships = small conflicts.
- `tools/ship "what changed + how to verify"` rebases on `origin/dev`, runs `tools/check`, fast-forwards
  `dev`, and posts DONE. Use `--breaking` when §4 applies. Never push to `dev` any other way. Never force-push.
- Rebase conflict (exit 2): resolve it yourself keeping both sides' intent. If the conflicting file is in
  someone else's folder and the intent is unclear, ask its owner (QUESTION) and work on something else meanwhile.
- Checks fail (exit 3): your change broke something, fix it. If it's pre-existing breakage, post BLOCKED to the owner.
- `dev` red (`[ci] BREAKING`): if your commit caused it, fix within minutes or `git revert` it and ship.
  Everyone else: don't ship on top of red unless your ship fixes it.
- You never push to `main`, never rewrite history, never delete branches other than your own.

## 4. Contracts & cross-boundary changes

- **Additive changes** (new field, new endpoint, new env var with default): just do it, update `contracts/`
  in the same ship, mention it in the DONE text.
- **Breaking changes** (rename/remove/change semantics): prefer additive + deprecate. If unavoidable:
  update all consumers yourself in the same ship if you can; otherwise `tools/ship --breaking --to <owners>`
  and describe the migration in one line.
- Editing another owner's folder: allowed for small, obviously needed fixes (a call site, an import).
  Anything bigger: QUESTION to the owner first. If no answer in ~20 min, do it and say so in DONE.

## 5. Autonomy: decide yourself vs. ask a human

Decide yourself (and log significant ones in DECISIONS.md): implementation details, libraries within the
stack, refactors in your folders, test data, UI details within the agreed flow, bug fixes anywhere.

Ask the humans (`QUESTION --to leo` or `--to louis`) and keep working on something else until answered:
- changing scope, the demo flow, or the challenge/track focus
- adding a new external service, paid API, or anything that costs money or needs an account
- secrets/credentials (never put them in code, posts, or logs; ask a human to add them to `.env`)
- deleting features or large amounts of others' work
- anything you can't undo

When idle: take the next unclaimed task in PLAN.md; if none, improve the demo path (bugs, speed, empty
states, seed data) - never start new features that aren't in PLAN.md.

## 6. Safety

- `main` is the judges' demo. Only humans touch it (`tools/promote`). The pre-push hook enforces this.
- Never commit `.env`, keys, tokens, partner data. The repo and the board may be public.
- Don't run destructive commands outside your worktree (`rm -rf`, DB drops on shared resources, `git reset --hard` on shared branches).
- Budget: don't loop. If the same approach fails 3 times, post BLOCKED and switch tasks.

## 7. CiteTrace rules (from the project plan, see DECISIONS.md)

Hostinger judges the **strength and honesty of the detection logic**, including false positives, more
than the UI. Every agent protects these invariants; breaking one is a bug, not a design choice.

- **Contract enums are fixed** (`contracts/api.md`). Claim verdicts: `supported`, `distorted`,
  `contradicted`, `not_mentioned`, `unsourced`, `not_checkable` (+ subtype `self_citation`,
  `social_media`). Article verdicts: `well_sourced`, `weakly_sourced`, `propaganda_pattern`,
  `insufficient_evidence`. UI text is English.
- **Quote first, verdict second.** The verify LLM must return the evidence passage verbatim. Code
  fuzzy-matches it against the fetched source text (rapidfuzz, threshold `FUZZY_THRESHOLD`, default 90).
  No match -> `supported` is downgraded to `not_checkable`. Never trust an LLM quote unchecked.
- **`not_checkable` never counts against an article.** Unreachable, paywalled, social (X/YouTube) and
  self-citations are neutral.
- **Two-signal rule** decides `propaganda_pattern` (thresholds in `backend/app/verdict.py`, one place
  only). A single bad claim must never produce `propaganda_pattern`.
- **Eval cases != demo cases.** Thresholds and prompts are tuned only on `eval/` data, never on the
  three demo URLs. Once the eval run starts, the verify model and prompt are frozen; changing them
  means re-running the eval and saying so on the board.
- **Audit trail:** every claim verdict is stored with evidence quote, fuzzy score, model, and
  `PROMPT_VERSION`. Bump `PROMPT_VERSION` on every prompt change.
- **Max 6 central claims** per article; sources are only links/attributions in the same sentence or
  paragraph as a claim. Same-domain links are `self_citation`, not evidence.
- **Cost + stability:** max `MAX_CONCURRENT_ANALYSES` (default 2) jobs at once, cache by normalised
  URL. The demo buttons load cached results and must work with all external APIs down.
- **`tools/check` never calls paid or external APIs** (LLM, Exa, Context.dev, RDAP). Tests use recorded
  fixtures in `backend/tests/fixtures/`. Real calls only in `eval/` and manual runs.
- **Methodology page is never cut.** If time runs short, drop the in-text claim highlighting first.
