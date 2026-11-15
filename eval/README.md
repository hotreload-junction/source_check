# Evaluation (PLAN E2-E4)

Numbers on the methodology page (`GET /api/methodology`) come from here. Eval items are disjoint from the demo cases;
no prompt or threshold was changed on them.

| Step | Command | Calls |
|---|---|---|
| pairs data | `python eval/build_pairs.py` | downloads Check-COVID + WiCE (public, expert labels) |
| article list | `python eval/build_articles.py` | Pravda network sitemaps + mainstream RSS feeds (labels = provenance) |
| claim check | `python eval/run_pairs.py` | shipped `verify_claim` on 100 pairs |
| article verdicts | `python eval/run_articles.py` | full shipped pipeline on 50 articles (+ label without the VIGINUM list, offline) |
| metrics | `python eval/metrics.py` | writes `eval/results/metrics.json` + `backend/demo/metrics.json` |

Run with the backend venv and `EVAL_ENV_FILE=<.env with VERDA_API_KEY, CONTEXT_DEV_API_KEY, EXA_API_KEY>`. The
credentials are loaded into the process and never written to results. Evaluation uses the same Verda-backed client as
the backend and an isolated prompt-response cache under `eval/runs/llm_cache`.
Set `EVAL_RESULTS_DIR` and `EVAL_RUNS_DIR` to run a scratch comparison without touching the published metrics or
historical result rows. Result rows carry a run id (provider, models, prompt version, screen status); resume and metrics
only use rows from the active run, and failed API calls are excluded and retried on the next start.

**Models.** One `deepseek-4-1-flash-1017` model handles extraction, helper calls and verification via Verda; candidate
pre-screening is disabled. Verda's default reasoning mode (`none`) is used. Successful LLM answers are cached by model
and full prompt so interrupted runs resume without repeating them. Display-only parts (chain search, depth >= 2 checks)
are off; they never reach `verdict.py`.

**Data licences.** Check-COVID (Wang et al., ACL Findings 2023, MIT), WiCE (Kamoi et al., EMNLP 2023, CC-BY-SA 4.0).
Article texts are not stored in the repo (`eval/runs/articles/`, gitignored).
