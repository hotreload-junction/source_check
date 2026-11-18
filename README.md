# Source Check

> Propaganda fakes its sources. We show you where.

Source Check shows whether the sources of a claim really back up what it says. Paste a link. It picks out the main claims, follows every source, and shows the exact sentence from the source as proof. Code checks that sentence against the real page, so the AI can't invent evidence. Source Check also looks at who runs the sites behind the sources, and whether they only cite each other. It calls an article propaganda only when two separate warning signs agree. Dead links and paywalls never count against it. Results are stored in MariaDB, and Verda runs the AI inference. We use Deepseek 4.1 Flash as the Brain in combination with Exa API to find the sources and evaluate the sources. Source Check runs live on Hostinger, and the methodology page shows our error rates.

Junction 2026 · Hostinger challenge · built with Exa, Context.dev, Verda, MariaDB on Hostinger.

## Repo map

| Path | What |
|---|---|
| `backend/` | Python 3.12, FastAPI pipeline + API |
| `frontend/` | UI: input, result (article + Cytoscape graph), methodology page |
| `contracts/` | API contract v1 + mock payloads |
| `eval/` | labelled eval data, harness, metrics |
| `deploy/` | Hostinger VPS deployment |
| `BRIEF.md`, `PLAN.md`, `DECISIONS.md`, `AGENTS.md` | challenge, tasks, decisions, rules for humans and agents |

## How we work

Two humans and four coding agents. Agents work on `agent/<handle>` branches, ship to `dev` with
`tools/ship`, and coordinate on a pinned GitHub issue via `tools/board`. Humans promote `dev` to `main`
with `tools/promote`. Rules: `AGENTS.md`.

The agent-coordination template (`tools/`, `AGENTS.md`, hooks) existed before the hackathon; all Source Check
code was written during Junction 2026.
