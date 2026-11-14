# Demo cache

`cases.json` lists the three demo cases (`key`, `label`, `url`, optional `expected_verdict`). The three entries now are
**placeholders** until Louis' E1 picks the real cases.

`results/<key>.json` holds the precomputed contract `Result` of one case. The demo buttons load these, so they work with
every external API down. **Results contain the full article text (`article.text_markdown`): do not commit them for
articles you may not republish** (the repo may be public). For the final demo cases that is a team decision;
commit with `git add -f` once it is made, or place the files on the server only.

Build or refresh (real network + LLM calls, manual only):

    cd backend && python -m app.demo_build            # all cases
    python -m app.demo_build --only pravda            # one case
    python -m app.demo_build --import pravda out.json # use the output of `python -m app.cli <url> -o out.json`

`demo_build` and `app.cli` use the persistent step cache (database): rebuilding an article that was analysed before replays
the stored LLM answers / pages / search results, so you get the SAME result; `--refresh` recomputes everything.

Results are frozen on purpose: re-running the pipeline can give a different verdict (LLM/search are not deterministic).
At startup the backend copies them into the database as jobs `demo-<key>`, so `POST /api/analyses` with a demo URL is
answered from the cache too. If the database is down, `GET /api/analyses/demo-<key>` is served from these files.
