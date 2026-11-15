"""Claim check on labelled pairs: the SHIPPED verify step (prompt, quote-first fuzzy guard) on public expert-labelled
claim/source pairs (eval/data/pairs.jsonl, built by build_pairs.py). Real model calls through the Claude CLI.

Resumable: pairs already in eval/results/pairs.jsonl are skipped. A pair that was running when the usage limit hit is
NOT written (it will run on the next start). Run: python eval/run_pairs.py [--limit N] [--workers 6]
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import DATA, RESULTS, append_jsonl, client, read_jsonl, settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    s = settings()
    from app.llm import LLMError
    from app.pipeline.verify import verify_claim

    llm = client(concurrency=args.workers)
    out = RESULTS / "pairs.jsonl"
    done = {r["id"] for r in read_jsonl(out)}
    todo = [p for p in read_jsonl(DATA / "pairs.jsonl") if p["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(done)} done, {len(todo)} to run")

    def one(p: dict) -> dict | None:
        t0 = time.monotonic()
        try:
            v = verify_claim(p["claim"], p["source_text"], llm=llm, settings=s)
            row = {"verdict": v.verdict, "llm_verdict": v.llm_verdict, "downgraded": v.downgraded, "fuzzy_score": v.fuzzy_score,
                   "explanation": v.explanation[:300], "model": v.model, "prompt_version": v.prompt_version}
        except LLMError as exc:
            if llm.limit_hit.is_set():
                return None
            row = {"verdict": "not_checkable", "llm_verdict": None, "downgraded": True, "fuzzy_score": None,
                   "explanation": f"LLM error: {str(exc)[:200]}", "model": s.model_verify, "prompt_version": s.prompt_version}
        return {"id": p["id"], "dataset": p["dataset"], "gold": p["gold"], "not_supported": p["not_supported"],
                "lang": p["lang"], **row, "seconds": round(time.monotonic() - t0, 1)}

    n = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(one, p) for p in todo]
        for f in as_completed(futs):
            r = f.result()
            if r is None:
                continue
            append_jsonl(out, r)
            n += 1
            if n % 10 == 0:
                print(f"  {n}/{len(todo)}  {llm.stats()}", flush=True)
    print(f"wrote {n} pairs; {llm.stats()}")
    if llm.limit_hit.is_set():
        print("STOPPED: subscription usage limit reached. Re-run later to continue.")


if __name__ == "__main__":
    main()
