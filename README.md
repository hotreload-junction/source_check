# CiteTrace

> Propaganda borrows credibility. CiteTrace checks the receipts.

CiteTrace takes a news article URL, extracts its central claims and follows every citation. For each claim
it checks whether the cited source actually supports it (supported / distorted / contradicted / not
mentioned / unsourced / not checkable) and shows the verbatim evidence passage. It fingerprints the
infrastructure behind each source (shared analytics/AdSense IDs, domain age) to reveal when "independent"
sources are run by the same operator. A "propaganda pattern" verdict needs at least two independent
signals; unreachable sources never count against an article.

Junction 2026 · Hostinger challenge · built with Exa, Context.dev, MariaDB on Hostinger.

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

The agent-coordination template (`tools/`, `AGENTS.md`, hooks) existed before the hackathon; all CiteTrace
code was written during Junction 2026.
