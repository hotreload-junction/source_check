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

Run with the backend venv and `EVAL_ENV_FILE=<.env with CONTEXT_DEV_API_KEY, EXA_API_KEY>`. Only those two keys are
read; detection settings stay at the code defaults (as on the VPS).

**Models.** Every model call goes through the Claude Code CLI (`claude -p`) logged in with the operator's own Claude
subscription (`eval/claude_cli.py`): `claude-sonnet-5-5` for verification, `claude-haiku-5-5` for extraction, effort
medium, our system prompt replaces the CLI's, no tools. Display-only parts (chain search, depth >= 2 checks) are off;
they never reach `verdict.py`. All runs resume where they stopped (answers cached in `eval/runs/`, gitignored); an item
that was running when the subscription limit hit is discarded, never counted half-checked.

**Data licences.** Check-COVID (Wang et al., ACL Findings 2023, MIT), WiCE (Kamoi et al., EMNLP 2023, CC-BY-SA 4.0).
Article texts are not stored in the repo (`eval/runs/articles/`, gitignored).
