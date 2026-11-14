# Exa Agent vs. our evidence search (unlinked, attributed claims)

Manual experiment, 14 Nov 2026. Real network, LLM and **paid** Exa calls (agent runs: 4.50 USD for 72 runs). Never run by `tools/check`.
Reproduce: `python -m experiments.exa_agent_corpus OUT.json` -> pick articles -> `python -m experiments.exa_agent_compare CORPUS OUT --efforts low,medium`
-> `python -m experiments.exa_agent_extra_arm CORPUS OUT OUT2` -> `python -m experiments.exa_agent_analyze OUT2`.

## Setup
- **18 articles, 18 different outlets, 36 claims** (2 per article): AP, Reuters, DW, BBC, NBC, CBS, NYT, Yahoo Finance, Mint, ThePrint,
  RT, Pravda network, CGTN, Anadolu-style (anews), Hankyoreh, WSWS, a local TV site, a tech-news site. Claims = the
  first two *attributed claims without a link* that our extraction picked ("X said ...", quotes, statements).
- Every method gets the same claim + its paragraph. **Every URL of every method goes through the SAME verification**: fetch, verify
  with occasion/venue context (quote first, rapidfuzz guard >= 90, `source_type`). The comparison is about what is VERIFIED.
- `current` = shipped search (planned queries, venue domain, exact quoted words; only venue / exact-quote / official-looking pages are verified).
  `current_sec` = the same search but plain news outlets are verified too (the fair baseline for "secondary sources").
  `agent_low` / `agent_medium` = `POST /agent/runs`, `effort` low (0.025 USD) / medium (0.10 USD), schema: found, sources[url, kind, quote].

## Results (36 claims)
| | original found | any verified evidence | URLs judged / claim | of them verified | cost / claim (Exa) | latency median / p90 |
|---|---|---|---|---|---|---|
| current (shipped) | 12 (33%) | 13 (36%) | 4.4 | 14% | 0.038 USD | 19 s / 42 s |
| current_sec | 13 (36%) | 24 (67%) | 8.0 | 27% | 0.038 USD | 22 s / 40 s |
| agent low | 12 (33%) | 30 (83%) | 2.7 | 64% | 0.025 USD | 34 s / 50 s |
| agent medium | 13 (36%) | 32 (89%) | 3.6 | 66% | 0.100 USD | 51 s / 81 s |

- **Originals**: all four find about the same number (12-13/36). The union of ours and the agent's is **17/36 (47%)**: they find different ones.
  Agent-only: Trump's own Truth Social post, press-conference transcripts (Rev, Roll Call Factbase), the NBC "Meet the Press" transcript,
  the General Staff's official Telegram channel. Ours-only: mid.ru, SEC/investor-relations pages, ministry sites.
- **Any evidence (incl. independent reports of the quote)**: the agent is clearly better (83-89% vs 67% for our search with secondary sources),
  with 3x fewer URLs and therefore ~3x fewer verify LLM calls. Union of all: 33/36. Nobody finds 3 claims (a Chinese state-media report on a NZ minister, a local TV quote).
- **Cost**: agent low is cheaper than our 7-search fan-out (0.025 vs 0.038 USD) before counting the extra verify calls ours needs.
- **Latency**: agent low +15 s median (p90 50 s, max 72 s). Claims run in parallel, so an article waits for its slowest claim.
- **Agent honesty**: 84% (low) / 94% (medium) of the quotes it hands over occur verbatim on the page (fuzzy >= 90); the rest is stitched together
  ("..."). It labels almost every source "original" (40/41) but only 14/40 (low) and 16/41 (medium) survive our `source_type` judgement.
  Never trust its labels or quotes: verification stays ours.

## Hand check of the cases only the agent finds (9)
Real independent confirmation: RT/Nvidia (NYT, The Verge), AP/Zelenskyy (Ukrinform), Trump (Roll Call transcript, UPI, BBC), Iran (Iran International),
Georgia (imedi.ge). **Not independent**: two Reuters/Healey claims were "confirmed" by four mirrors of the same Reuters text. One weak match (Sherrod Brown:
the quoted passage does not show the claim). The last one is a limit of LLM verification that affects every method.

## Bugs in OUR code this experiment exposed (fixed in the same commit)
1. A post by ANY account on a user-generated platform counted as "the venue's own page" (a fan account's Truth Social post was an "original"). Platforms
   (truthsocial, x, youtube, t.me, ...) are never the venue's own site now; the model's `source_type` decides.
2. **Syndicated copies counted as independent reporting**: 15% of all "confirming" pages (16 of 109 measurable; both methods) repeat >= 40% of the
   article's other sentences. They are dropped now (`syndication_share`, threshold 0.4), only for non-original pages.

## Limits of this experiment (read before deciding)
36 claims, one run per arm (the agent is not deterministic), no human ground truth (verification is the same LLM for every arm, so shared
bias cancels but is not removed), 18 outlets of one week. Original counts are small numbers: 12 vs 13 is no difference; 17 vs 13 is a hint, not a proof.

## Recommendation
Use the agent as an ADDITIONAL discovery source (effort `low`, ~0.025 USD and ~35 s per attributed claim), run in parallel with our targeted
searches, one shared verification (top 5 URLs, ours ranked by exact-quote/venue first). Expected: any evidence 36% -> ~90%, originals 33% -> up to ~45%.
Needs: step cache for agent runs (replays), our own `source_type`, the syndication filter, a cost/concurrency guard (50 runs, 5 starts/s).
It is a new paid API (AGENTS.md section 5): needs a human decision.
