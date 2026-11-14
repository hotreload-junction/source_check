"""Build / refresh the demo cache: `python -m app.demo_build [--only KEY] [--import KEY FILE]`.

Real network + LLM calls (manual only, never tools/check). Writes backend/demo/results/<key>.json and compares the
verdict with `expected_verdict` from cases.json when the case defines one."""
from __future__ import annotations

import argparse
import json
import sys

from . import demo
from .config import get_settings
from .pipeline.run import AnalysisError, analyze


def _write(key: str, result: dict, settings) -> None:
    path = demo.demo_dir(settings) / "results" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.demo_build", description=__doc__)
    ap.add_argument("--only", help="build just this case key")
    ap.add_argument("--refresh", action="store_true", help="ignore the step cache and recompute (the result will usually differ)")
    ap.add_argument("--import", dest="imp", nargs=2, metavar=("KEY", "FILE"), help="store the JSON written by `python -m app.cli -o`")
    args = ap.parse_args(argv)
    s = get_settings()
    if s.step_cache:      # a case that was analysed before is rebuilt from the cache: same inputs, same result
        from . import cache
        cache.set_default(cache.DbCache(ttl_hours=s.step_cache_ttl_h or None))
    cases = json.loads((demo.demo_dir(s) / "cases.json").read_text(encoding="utf-8"))
    if args.imp:
        key, path = args.imp
        data = json.loads(open(path, encoding="utf-8").read())
        result = data.get("result", data)
        if not {"article", "claims", "sources", "domains", "graph", "signals", "verdict", "audit"} <= set(result):
            print("error: not a contract Result / Analysis JSON", file=sys.stderr)
            return 2
        _write(key, result, s)
        print(f"{key}: imported, verdict {result['verdict']['label']}")
        return 0
    failed = 0
    for c in cases:
        if not isinstance(c, dict) or not (c.get("key") and c.get("url")):
            print(f"skipping malformed case: {c!r}", file=sys.stderr)
            continue
        if args.only and c["key"] != args.only:
            continue
        print(f"{c['key']}: analysing {c['url']} ...", file=sys.stderr, flush=True)
        try:
            out = analyze(c["url"], settings=s, refresh=args.refresh)
        except AnalysisError as exc:
            print(f"{c['key']}: FAILED ({exc})", file=sys.stderr)
            failed += 1
            continue
        _write(c["key"], out.result, s)
        got, exp = out.result["verdict"]["label"], c.get("expected_verdict")
        note = "" if not exp else ("  (matches expected)" if got == exp else f"  (!! expected {exp})")
        print(f"{c['key']}: {got} in {out.result['audit']['duration_s']}s{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
