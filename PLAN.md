# PLAN

## Demo flow (what the judges see, in order)

1. **Input page:** URL field + 3 demo buttons (load from cache, work with all external APIs down).
2. **Live analysis:** progress steps stream in, the graph builds up node by node (article -> claims -> sources -> domains).
3. **Result:** left the article with claims coloured by verdict, right the Cytoscape graph; domains run by the
   same operator form coloured clusters. Click a claim -> verbatim evidence passage (original + translation)
   and a one-sentence explanation. Article verdict on top with the signals that fired.
4. **The three cases:** Pravda article -> `propaganda_pattern`; Reuters/AP -> `well_sourced` (false-positive
   proof); sloppy blog with dead links -> `weakly_sourced`.
5. **Methodology page:** precision, recall, FPR, confusion matrix, error cases, thresholds ("set on X, measured on Y").

## Checkpoints (hard rule: missing one by > 2 h cuts the next stage, not sleep)

| When | Goal | Tasks |
|---|---|---|
| ~~Fri 02:00~~ | VPS live, MariaDB container, template repo | T0 ✅ |
| **Sat 12:00** | depth 1 end-to-end on the CLI for the 3 demo cases | B1-B7 |
| **Sat 18:00** | depth 1 deployed with UI, eval data labelled | B8-B9, F1-F4, D1, E1-E3 |
| **Sat 18:00-01:00** | depth 2, MariaDB Vector, eval run, methodology page | M1-M2, E4-E5, F5 |
| **Sun 08:00** | **code freeze** (only humans promote after this) | |
| Sun 08:00-10:00 | submission text, screenshots/video, pitch practice | S1-S3 |
| **Sun 10:30** | peer review, in person | |

Cut order if late: M3 -> M2 -> M1 -> in-text claim highlighting (F3 part). **Never cut F5 / E4.**

## Tasks

Claim by putting your handle in `owner` and shipping that edit. Status: todo / doing / done / blocked.
Owners below are the default split (AGENTS.md section 0); re-claim freely if someone is idle.

### Setup
| id | task | owner | status | notes |
|---|---|---|---|---|
| T0 | VPS live, MariaDB container, template repo | leo, louis | done | Fri |
| T1 | agree contract v1 (`contracts/api.md`, example JSON, `.env.example`) | leo, louis | doing | review before splitting up; changes after that per AGENTS.md section 4 |
| T2 | `tools/setup` (dev branch, pinned board issue), one worktree per agent | leo | todo | `tools/worktree leo-be` etc. |
| T3 | API keys tested (AI Router, Exa, Context.dev) and in `.env` locally + on VPS | leo | todo | humans only: secrets |
| T4 | spike: does Context.dev return markdown **with links kept**? test on the Pravda demo article | leo-be | todo | prerequisite for Q18; if not, trafilatura with `include_links` |

### Backend: depth 1 (checkpoint Sat 12:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| B1 | skeleton: FastAPI, config from env, `/api/health`, pytest, MariaDB schema incl. audit trail | leo-be | done | `backend/`, `pyproject.toml` with `[dev]` extra |
| B2 | fetch layer: Context.dev + trafilatura fallback, Telegram `t.me/s/`, X/YouTube -> `social_media`, same domain -> `self_citation`, dead/paywall -> `not_checkable` | leo-be | done | record fixtures for tests |
| B3 | claim extraction (cheap LLM, max 6), attach links/attributions from same sentence/paragraph, named unlinked source -> Exa | leo-be | done | JSON output, temperature 0 |
| B4 | verify (strong LLM): verbatim quote original + translation + verdict; rapidfuzz check vs source text, downgrade on miss | leo-be | done | store `PROMPT_VERSION`, model, score |
| B5 | infra signals: GA/GTM/AdSense IDs via regex on raw HTML, RDAP domain age, operator clusters | leo-bg | done | `backend/app/signals/` |
| B6 | `backend/app/verdict.py`: two-signal rule + unit tests for every row of the table | leo-bg | done | thresholds as named constants, one place |
| B7 | CLI `python -m app.cli <url>` -> JSON per contract; run on the 3 demo cases | leo-be | done | **checkpoint Sat 12:00** |

### Backend: API (checkpoint Sat 18:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| B8 | jobs: `POST /api/analyses`, `GET /api/analyses/{id}`, SSE events, background tasks with status in DB, concurrency limit, URL cache | leo-be | done | see contract |
| B9 | demo cache: precompute the 3 demo cases, `GET /api/demo`, `GET /api/methodology` from `eval/results/metrics.json` | leo-be | done | must work offline |

### Frontend (checkpoint Sat 18:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| F1 | skeleton against `contracts/examples/*.json` mocks; pick stack, log it in DECISIONS.md | louis-fe | done | Vite+React+TS; `npm run dev` / `npm run dev:mock` |
| F2 | input page: URL field + 3 demo buttons | louis-fe | done | UI done: landing restyled (composer chip + optional question, "Try asking" rows). Demo cases are bundled in the frontend (contract fixtures, fictional domains) and used while `GET /api/demo` is empty/down; backend cases win once B9/E1 produce results. Cached results reveal in 3 s |
| F3 | result view: article with coloured claims (left), Cytoscape graph (right), operator clusters, click claim -> evidence | louis-fe | doing | Source Trace design (DECISIONS Sat 13:45), own renderer instead of Cytoscape. Done: operator-cluster dashed box ("same operator as the article · <shared ID>"), click claim in article text (root panel). Open: article-verdict bar on top (`bar.ts` helpers on dev, component not built), test on real result data (B8) |
| F4 | live progress via SSE (fallback polling every 2 s), graph builds incrementally | louis-fe | doing | built, untested against real backend (B8 pending) |
| F5 | methodology page: precision, recall, FPR, confusion matrix, error cases, thresholds | louis-fe, leo-fe | doing | **never cut.** Route `#/methodology`, linked as "How it works" on landing + result top bar. Sections: pipeline (8 steps, each with its false-positive guard), claim verdict meanings, signals + rule chain (values live from `/api/methodology`), FP guards with real cases from DECISIONS, metrics (empty state until E4), repeatability (dev measurements), source labels, known limits, audit trail, tech stack (short list, last section). Open: real numbers from E4; network recall with/without the VIGINUM list (DECISIONS laundering line) needs a contract field |
| F6 | reader focus: optional question/concern next to the URL; the sentence(s) it is about are located (majority vote) and checked like every claim; never changes the article verdict | leo-bg | done | backend `pipeline/focus.py` + API `question`, frontend field on landing + top bar, focus banner, dashed claim outline; contract `Result.focus`, `Claim.central/focus` (feedback Sat evening, leo asked) |
| F7 | top bar "Propaganda <-> Objective": one big gradient slider (position = article verdict zone nudged by supported share, `bar.ts`); click opens a two-column table (objectivity green / propaganda red) with rows quality of sources, claims match sources, variety, wording, other signals; cell colour strength = argument strength (`trust.ts`, `TrustPanel.tsx`); also closes the F3 "article-verdict bar on top" | louis-fe | doing | context only, never a verdict signal; rule-based, no LLM; wording = frontend word list (24 English terms + exclamation marks), shown with its matches |

### Deploy
| id | task | owner | status | notes |
|---|---|---|---|---|
| D1 | `deploy/`: docker compose (mariadb, backend + static frontend), reverse proxy + HTTPS, public URL | louis-bg, louis-fe | doing | stack runs locally: `docker compose -f deploy/docker-compose.yml up --build` (db, backend by leo-be, web = Caddy). Open: VPS setup, HTTPS on the real domain (set `SITE_ADDRESS`), public URL |
| D2 | deploy script: VPS pulls `main` and restarts (`deploy/deploy.sh`), smoke test `/api/health` + demo case | louis-bg | todo | humans run it after `tools/promote` |
| D3 | automated deploy: Action on push main -> live, dev -> preview over SSH, deploy.sh with smoke test + rollback, edge proxy, bootstrap | leo-bg | done (VPS, DNS, secrets set up 14 Nov; first run = this push) | `.github/workflows/deploy.yml`, `deploy/deploy.sh`, `deploy/bootstrap.sh`, `deploy/edge/`, `docs/deploy.md`. Needs humans: DNS, SSH key secrets (docs/deploy.md). Covers D2 |

### Evaluation
| id | task | owner | status | notes |
|---|---|---|---|---|
| E1 | pick 3 demo cases (Pravda, Reuters/AP, sloppy harmless blog) -> `eval/demo_cases.md` | louis | todo | Sat morning, DFRLab/CheckFirst lists |
| E2 | label 40 claim-source pairs (~half supported, half distorted/not mentioned) -> `eval/pairs.jsonl` | leo-fe | done | replaced by 100 public expert-labelled pairs (Check-COVID + WiCE), `eval/build_pairs.py` -> `eval/data/pairs.jsonl`; 0 cross-language pairs |
| E3 | 10 network + 10 wire-service articles -> `eval/articles.csv`, **disjoint from demo cases** | leo-fe | done | 25 network (Pravda sitemaps) + 25 mainstream incl. 8 hard negatives, `eval/build_articles.py` -> `eval/data/articles.csv` |
| E4 | eval harness: verify step on pairs (precision/recall for "not supported"), article verdicts (FPR on wire articles) -> `eval/results/metrics.json` + error cases | leo-fe | doing | built (`eval/`, see README). Pairs 100/100; articles 30/50 (count set by leo for the subscription limit). Resume: `python eval/run_articles.py && python eval/metrics.py` |
| E5 | Q21: compare models, freeze verify model + prompt, then run E4 for real | leo | todo | optional cheap-vs-strong comparison for methodology page |

### MVP extensions (Sat 18:00-01:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| M1 | depth 2: steps 2-5 for the sources' sources, citation-circle signal | leo-bg | done | `MAX_DEPTH=2` in `.env`; code only, see DECISIONS |
| M2 | MariaDB Vector: embed source texts, "seen this text on N other domains" | leo-be | todo | |
| M3 | Exa findSimilar for copies of the story | louis-fe | done | ported Sat (DECISIONS); copies join the infra signal, see Q3 |

### Source quality (added Sat, leo)
Context for the reader, not a verdict signal (DECISIONS "source quality"). Labels with provenance, no numeric score.
| id | task | owner | status | notes |
|---|---|---|---|---|
| Q1 | source type per Source: deterministic rule table (official / press_release / academic / reference / news / blog / social / unknown) + the rule that fired | leo-bg | done | `backend/app/signals/source_type.py`, additive contract fields `source_type`, `source_type_reason`; UI (leo-bg): type in the source card meta line + panel row, `frontend/src/labels.ts` |
| Q2 | domain reputation badges: Wikipedia Perennial Sources + Tranco top-100k as dated snapshots in the repo, Wayback first capture (CDX, step-cached) vs RDAP age (expired-domain reuse) | leo-bg | done | `signals/reputation.py`, snapshots in `signals/data/` (rebuild: `python -m app.signals.reputation_build`); Domain fields `wikipedia_rsp`, `tranco_rank`, `first_archived_at`, `reputation_notes`; UI (leo-bg): card labels (Wikipedia rating, re-registered), panel section "Source type and reputation", methodology section |
| Q3 | harden copies (M3): a copy only feeds the infra signal when it shares an ID with the ARTICLE (not copy-to-copy) and copy domain age never fires "young"; label copies as syndication (wire attribution / Tranco-listed) vs mirror | | todo | FP risk: a young scraper mirroring a Reuters article fires infra for Reuters today |
| Q4 | Wikidata (instance of news agency / state media, owner) + crt.sh shared certificates | | todo | only if time |

### Submission (Sun 08:00-10:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| S1 | pitch + demo script: 2-min peer-review version, 3-min top-5 version | louis | todo | Sat evening |
| S2 | submission text (draft in project doc), screenshots/video; disclose that the agent-setup template existed before the hackathon | louis | todo | |
| S3 | read side-quest rules (Exa, Context.dev, MariaDB) on the Junction platform | louis | todo | |
