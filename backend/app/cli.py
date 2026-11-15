"""CLI: `python -m app.cli <url>` -> contract `Analysis` JSON on stdout (progress on stderr).

Real network + LLM calls: manual runs only (AGENTS.md section 7), never from tools/check.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from .config import get_settings
from .pipeline.run import AnalysisError, analyze


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="Analyse how well a news article backs its claims.")
    ap.add_argument("url", help="article URL")
    ap.add_argument("-o", "--out", help="write the JSON here instead of stdout")
    ap.add_argument("--compact", action="store_true", help="single-line JSON")
    ap.add_argument("--refresh", action="store_true", help="ignore the step cache: re-fetch, re-ask the models, re-search (and store the new result)")
    ap.add_argument("--no-cache", action="store_true", help="do not use the database step cache at all")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress on stderr")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(name)s: %(message)s")
    s = get_settings()
    missing = s.llm_missing
    if missing:
        print(f"error: missing in .env: {', '.join(missing)}", file=sys.stderr)
        return 2

    if s.step_cache and not args.no_cache:
        from . import cache
        cache.set_default(cache.DbCache(ttl_hours=s.step_cache_ttl_h or None))
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    last = {"pct": 0, "message": ""}

    def emit(event: str, data: dict) -> None:
        if event == "progress":
            last.update(pct=data["pct"], message=data["message"])
            if not args.quiet:
                print(f"[{data['pct']:3d}%] {data['message']}", file=sys.stderr, flush=True)

    try:
        out = analyze(args.url, settings=s, emit=emit, refresh=args.refresh)
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    analysis = {"job_id": "cli", "status": "done", "progress": {"step": "verdict", "message": "Done", "pct": 100},
                "error": None, "created_at": created,
                "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "result": out.result}
    text = json.dumps(analysis, ensure_ascii=False, indent=None if args.compact else 2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    if not args.quiet:
        v = out.result["verdict"]
        print(f"\n{v['label']}: {v['summary']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
