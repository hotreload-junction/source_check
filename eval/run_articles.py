"""Article verdicts: the full SHIPPED pipeline (fetch, claims, sources, verify, infrastructure, depth, verdict.py) on
articles labelled by provenance (eval/data/articles.csv, built by build_articles.py). Real network, Exa and model calls
(models through the Claude CLI). Display-only parts are off (common.OVERRIDES); verdicts are unaffected.

For every article we also record the label WITHOUT the published network list (VIGINUM Portal Kombat): the same
signals minus the list-based infrastructure reasons, decided by the unchanged rule in verdict.py. Network articles are
on that list by construction, so only the "without" number shows what the detection does on its own.

Resumable; an article that was running when the usage limit hit is discarded and runs again next time.
Full results (with the third-party article text) stay in eval/runs/articles/ (gitignored, DECISIONS: no republishing).
Run: python eval/run_articles.py [--limit N] [--workers 2]
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import DATA, RESULTS, ROOT, RUNS, append_jsonl, client, read_jsonl, run_id, settings

LIST_MARK = "is on the published list of the"


def without_list(result: dict) -> tuple[str, list[str]]:
    from app.verdict import SIGNAL_ORDER, ClaimInput, _label

    sig = json.loads(json.dumps(result["signals"]))
    infra = sig["infrastructure"]
    infra["reasons"] = [r for r in infra.get("reasons", []) if LIST_MARK not in r]
    infra["fired"] = bool(infra["reasons"])
    claims = [ClaimInput(c["id"], c["verdict"], c["verdict"] == "supported" and not c.get("source_ids") and bool(c.get("found_source_ids")))
              for c in result["claims"] if c.get("central", True)]
    return _label(claims, sig)["label"], [k for k in SIGNAL_ORDER if sig[k]["fired"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--llm-concurrency", type=int, default=6)
    args = ap.parse_args()
    s = settings()
    current_run = run_id(s)
    from app.pipeline.run import AnalysisError, analyze
    from app.verdict import SIGNAL_ORDER

    import subprocess
    code = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    llm = client(concurrency=args.llm_concurrency)
    out = RESULTS / "articles.jsonl"
    done = {r["id"] for r in read_jsonl(out) if r.get("run_id") == current_run and r.get("status") == "done"}
    with (DATA / "articles.csv").open(encoding="utf-8") as f:
        todo = [r for r in csv.DictReader(f) if r["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(done)} done, {len(todo)} to run")
    (RUNS / "articles").mkdir(parents=True, exist_ok=True)

    def one(a: dict) -> dict | None:
        if llm.limit_hit.is_set():
            return None
        t0, cost0 = time.monotonic(), llm.cost_usd
        base = {"id": a["id"], "run_id": current_run, "url": a["url"], "group": a["group"], "outlet": a["outlet"], "hard": a["hard"] == "True", "code": code}
        try:
            res = analyze(a["url"], settings=s, llm=llm).result
        except AnalysisError as exc:
            return {**base, "status": "failed", "error": str(exc)[:200], "seconds": round(time.monotonic() - t0)}
        if llm.limit_hit.is_set():          # some claims may have been cut off by the limit: never count this article
            return None
        (RUNS / "articles" / f"{a['id']}.json").write_text(json.dumps(res, ensure_ascii=False))
        label_wo, fired_wo = without_list(res)
        central = [c for c in res["claims"] if c.get("central", True)]
        return {**base, "status": "done", "label": res["verdict"]["label"], "summary": res["verdict"]["summary"],
                "fired": [k for k in SIGNAL_ORDER if res["signals"][k]["fired"]],
                "infra_reasons": res["signals"]["infrastructure"].get("reasons", [])[:6],
                "label_without_list": label_wo, "fired_without_list": fired_wo,
                "claim_verdicts": [c["verdict"] for c in central], "n_sources": len(res.get("sources", [])),
                "seconds": round(time.monotonic() - t0), "approx_usd_equivalent": round(llm.cost_usd - cost0, 2)}

    n = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(one, a): a for a in todo}
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as exc:        # a pipeline crash is recorded, never silently dropped
                a = futs[f]
                r = {"id": a["id"], "run_id": current_run, "url": a["url"], "group": a["group"], "outlet": a["outlet"], "hard": a["hard"] == "True",
                     "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            if r is None:
                continue
            append_jsonl(out, r)
            n += 1
            print(f"  [{n}/{len(todo)}] {r['id']} {r['group']:7s} {r.get('label', r['status']):22s} "
                  f"w/o list: {r.get('label_without_list', '-'):22s} {r.get('seconds', '?')}s  {llm.stats()}", flush=True)
    print(f"wrote {n} articles; {llm.stats()}")
    if llm.limit_hit.is_set():
        print("STOPPED: subscription usage limit reached. Re-run later to continue.")


if __name__ == "__main__":
    main()
