# BRIEF

**CiteTrace** · Junction 2026 · Hostinger challenge
> Propaganda borrows credibility. CiteTrace checks the receipts.

Paste the verbatim Hostinger challenge text at the marker below when you have it (Junction platform).
Until then the summary is from the team plan.

## Challenge / track

Hostinger: build an AI tool that detects coordinated propaganda, bot amplification or bad actors online,
and **deploy it live on Hostinger infrastructure with a public URL**. Hostinger values the **strength and
honesty of the detection logic** (including how false positives are handled) over a polished frontend.
Wanted: show **precision and false-positive rate**, not just the happy path.

Our angles:
- **Trust the source** (provenance, cloned content, link networks): core
- **Name the technique** ("fabricated authority", verdict explained in plain language): core
- **Spot the swarm** (coordinated behaviour): partly, via shared infrastructure and citation circles
- Clean the traffic: out of scope

What we build: input a news article URL -> extract up to 6 central claims -> follow every citation ->
check whether the cited source actually supports the claim, with the verbatim evidence passage ->
fingerprint the infrastructure behind each source (analytics/AdSense IDs, domain age) -> article verdict
by the two-signal rule (AGENTS.md section 7, DECISIONS.md).

<!-- verbatim Hostinger challenge text here -->

## Judging criteria (optimise for these)

- **Innovation:** new or different compared to what exists? Our gap: no tool follows a news article's
  sources over several hops, checks at each hop whether the evidence holds, and detects citation circles.
  Not a "fact checker" (checks truth via fresh search), not a network detector (Information Laundromat,
  NewsGuard exist): we check the *cited* sources.
- **Impact:** does it significantly help the people facing the problem? Users: fact-checkers, journalists.
- **Implementation:** realistic, viable, feasible. For Hostinger: live URL, honest metrics, FP handling.
- The **top 5 are chosen by peer review** (other hackers, Sun 10:30-14:00, in person, ~2 min per table).
  **Hostinger picks the challenge winner.** So the demo must land in 2 minutes for a hacker *and* the
  methodology must convince Hostinger.

## Partner APIs, datasets, docs

| What | Role in the pipeline | Env / folder |
|---|---|---|
| Hostinger VPS | hosting, public URL | `deploy/` |
| Hostinger AI Router | LLM: cheap model for extraction, strong model for verification | `AI_ROUTER_*`, `MODEL_*` |
| Context.dev | scrape article + sources as markdown **with links kept** (fallback: trafilatura) | `CONTEXT_DEV_API_KEY` |
| Exa | find named-but-unlinked sources; optional findSimilar for copies of the story | `EXA_API_KEY` |
| MariaDB (+ Vector) | main store with audit trail; vector memory across analyses | `DATABASE_URL` |
| RDAP | domain age | none |
| Telegram web preview | `t.me/s/<channel>` for public posts | none |
| DFRLab / CheckFirst Pravda lists | source for demo + eval network articles | `eval/` |

Side quests: Exa, Context.dev, MariaDB. Check their rules on the Junction platform (open item in PLAN.md).

## Demo must show

1. Paste a URL (or click a demo button) -> live progress, the graph builds up step by step.
2. **Pravda-network article** -> `propaganda_pattern`: claims marked `distorted` / `not_mentioned` with the
   verbatim evidence passage, source domains clustered by shared analytics/AdSense IDs.
3. **Reuters/AP article** -> `well_sourced`: the live proof against false positives.
4. **Sloppy but harmless blog post** with dead links -> `weakly_sourced`, not propaganda (dead links are neutral).
5. **Methodology page**: precision, recall, FPR, confusion matrix, error cases, thresholds disclosed.
