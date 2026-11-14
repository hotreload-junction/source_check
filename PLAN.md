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
| B4 | verify (strong LLM): verbatim quote original + translation + verdict; rapidfuzz check vs source text, downgrade on miss | leo-be | todo | store `PROMPT_VERSION`, model, score |
| B5 | infra signals: GA/GTM/AdSense IDs via regex on raw HTML, RDAP domain age, operator clusters | leo-bg | done | `backend/app/signals/` |
| B6 | `backend/app/verdict.py`: two-signal rule + unit tests for every row of the table | leo-bg | done | thresholds as named constants, one place |
| B7 | CLI `python -m app.cli <url>` -> JSON per contract; run on the 3 demo cases | leo-be | todo | **checkpoint Sat 12:00** |

### Backend: API (checkpoint Sat 18:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| B8 | jobs: `POST /api/analyses`, `GET /api/analyses/{id}`, SSE events, background tasks with status in DB, concurrency limit, URL cache | leo-be | todo | see contract |
| B9 | demo cache: precompute the 3 demo cases, `GET /api/demo`, `GET /api/methodology` from `eval/results/metrics.json` | leo-be | todo | must work offline |

### Frontend (checkpoint Sat 18:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| F1 | skeleton against `contracts/examples/*.json` mocks; pick stack, log it in DECISIONS.md | louis-fe | done | Vite+React+TS; `npm run dev` / `npm run dev:mock` |
| F2 | input page: URL field + 3 demo buttons | louis-fe | done | top bar of the result view |
| F3 | result view: article with coloured claims (left), Cytoscape graph (right), operator clusters, click claim -> evidence | louis-fe | doing | Source Trace design (DECISIONS Sat 13:45); clusters shown in source panel + orange flag, no compound box yet |
| F4 | live progress via SSE (fallback polling every 2 s), graph builds incrementally | louis-fe | doing | built, untested against real backend (B8 pending) |
| F5 | methodology page: precision, recall, FPR, confusion matrix, error cases, thresholds | louis-fe | todo | **never cut** |

### Deploy
| id | task | owner | status | notes |
|---|---|---|---|---|
| D1 | `deploy/`: docker compose (mariadb, backend + static frontend), reverse proxy + HTTPS, public URL | louis-bg | todo | Hostinger VPS; secrets only in `.env` on the box |
| D2 | deploy script: VPS pulls `main` and restarts (`deploy/deploy.sh`), smoke test `/api/health` + demo case | louis-bg | todo | humans run it after `tools/promote` |

### Evaluation
| id | task | owner | status | notes |
|---|---|---|---|---|
| E1 | pick 3 demo cases (Pravda, Reuters/AP, sloppy harmless blog) -> `eval/demo_cases.md` | louis | todo | Sat morning, DFRLab/CheckFirst lists |
| E2 | label 40 claim-source pairs (~half supported, half distorted/not mentioned) -> `eval/pairs.jsonl` | louis | todo | Sat 10-14; count cross-language pairs |
| E3 | 10 network + 10 wire-service articles -> `eval/articles.csv`, **disjoint from demo cases** | louis | todo | |
| E4 | eval harness: verify step on pairs (precision/recall for "not supported"), article verdicts (FPR on wire articles) -> `eval/results/metrics.json` + error cases | louis-bg | todo | real API calls allowed here only |
| E5 | Q21: compare models, freeze verify model + prompt, then run E4 for real | leo | todo | optional cheap-vs-strong comparison for methodology page |

### MVP extensions (Sat 18:00-01:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| M1 | depth 2: steps 2-5 for the sources' sources, citation-circle signal | leo-bg | todo | |
| M2 | MariaDB Vector: embed source texts, "seen this text on N other domains" | leo-be | todo | |
| M3 | Exa findSimilar for copies of the story | | todo | only if time |

### Submission (Sun 08:00-10:00)
| id | task | owner | status | notes |
|---|---|---|---|---|
| S1 | pitch + demo script: 2-min peer-review version, 3-min top-5 version | louis | todo | Sat evening |
| S2 | submission text (draft in project doc), screenshots/video; disclose that the agent-setup template existed before the hackathon | louis | todo | |
| S3 | read side-quest rules (Exa, Context.dev, MariaDB) on the Junction platform | louis | todo | |
