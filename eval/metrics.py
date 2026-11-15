"""eval/results/{pairs,articles}.jsonl -> eval/results/metrics.json (contract `Methodology`, contracts/api.md) and a copy in
backend/demo/metrics.json (the backend image only contains backend/, the demo must work offline).

Every error case is listed, not a selection. Confidence intervals are left to the reader of the counts (the page
computes Wilson intervals from the confusion matrices). Run: python eval/metrics.py
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from common import EFFORT, MODEL_EXTRACT, MODEL_VERIFY, RESULTS, ROOT, read_jsonl, settings

FLAGGED = {"distorted", "contradicted", "not_mentioned"}      # the verify step says: the source does not support it


def confusion(rows, truth, pred) -> dict:
    c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for r in rows:
        t, p = truth(r), pred(r)
        c[("t" if t == p else "f") + ("p" if p else "n")] += 1
    return c


def ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


def main() -> None:
    s = settings()
    from app.verdict import thresholds

    pairs = read_jsonl(RESULTS / "pairs.jsonl")
    pc = confusion(pairs, lambda r: r["not_supported"], lambda r: r["verdict"] in FLAGGED)
    by_ds = {}
    for ds in sorted({r["dataset"] for r in pairs}):
        rows = [r for r in pairs if r["dataset"] == ds]
        c = confusion(rows, lambda r: r["not_supported"], lambda r: r["verdict"] in FLAGGED)
        by_ds[ds] = {"n": len(rows), "precision": ratio(c["tp"], c["tp"] + c["fp"]), "recall": ratio(c["tp"], c["tp"] + c["fn"]),
                     "confusion": c, "gold_vs_verdict": {f"{g} -> {v}": n for (g, v), n in
                                                         sorted(Counter((r["gold"], r["verdict"]) for r in rows).items())}}

    arts = read_jsonl(RESULTS / "articles.jsonl")
    done = [a for a in arts if a["status"] == "done"]
    net, wire = [a for a in done if a["group"] == "network"], [a for a in done if a["group"] == "wire"]
    is_net = lambda a: a["group"] == "network"
    ac = confusion(done, is_net, lambda a: a["label"] == "propaganda_pattern")
    acw = confusion(done, is_net, lambda a: a["label_without_list"] == "propaganda_pattern")
    hard = [a for a in wire if a["hard"]]

    errors = []
    for r in pairs:
        if (r["verdict"] in FLAGGED) != r["not_supported"]:
            errors.append({"kind": "fp" if r["verdict"] in FLAGGED else "fn", "url_or_pair_id": r["id"],
                           "expected": f"{r['gold']} ({'not supported' if r['not_supported'] else 'supported'})",
                           "got": r["verdict"], "note": r["explanation"][:240]})
    for a in done:
        flagged = a["label"] == "propaganda_pattern"
        if flagged != is_net(a):
            errors.append({"kind": "fp" if flagged else "fn", "url_or_pair_id": a["url"],
                           "expected": "propaganda_pattern" if is_net(a) else "not propaganda_pattern",
                           "got": a["label"], "note": a["summary"][:240]})

    m = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verify_model": f"{MODEL_VERIFY} (extraction {MODEL_EXTRACT}; effort {EFFORT}; called through the Claude Code CLI)",
        "prompt_version": s.prompt_version,
        "pairs": {"n": len(pairs), "n_cross_language": sum(1 for r in pairs if r.get("lang") != "en"),
                  "precision": ratio(pc["tp"], pc["tp"] + pc["fp"]), "recall": ratio(pc["tp"], pc["tp"] + pc["fn"]),
                  "confusion": pc, "abstained": sum(1 for r in pairs if r["verdict"] == "not_checkable"), "by_dataset": by_ds},
        "articles": {"n_network": len(net), "n_wire": len(wire),
                     "fpr_wire": ratio(ac["fp"], len(wire)), "tpr_network": ratio(ac["tp"], len(net)), "confusion": ac,
                     "fpr_wire_without_list": ratio(acw["fp"], len(wire)), "tpr_network_without_list": ratio(acw["tp"], len(net)),
                     "confusion_without_list": acw,
                     "n_hard_negatives": len(hard), "fp_hard_negatives": sum(1 for a in hard if a["label"] == "propaganda_pattern"),
                     "n_failed": sum(1 for a in arts if a["status"] != "done"),
                     "labels": {g: dict(Counter(a["label"] for a in done if a["group"] == g)) for g in ("network", "wire")},
                     "labels_without_list": {g: dict(Counter(a["label_without_list"] for a in done if a["group"] == g)) for g in ("network", "wire")}},
        "thresholds": {**thresholds(), "fuzzy": s.fuzzy_threshold},
        "tuned_on": "nothing: thresholds are start values set on principle before this run, and no prompt or threshold was changed on these items",
        "measured_on": (f"{len(pairs)} expert-labelled claim-source pairs (Check-COVID, WiCE) and {len(done)} articles labelled by provenance "
                        f"({len(net)} from the Pravda network's own sitemaps, {len(wire)} from BBC, DW, NPR, Guardian, Al Jazeera, "
                        f"Kyiv Independent, Ukrainska Pravda and Meduza feeds), collected 15 Nov 2026"),
        "error_cases": errors,
    }
    text = json.dumps(m, ensure_ascii=False, indent=2) + "\n"
    (RESULTS / "metrics.json").write_text(text, encoding="utf-8")
    (ROOT / "backend" / "demo" / "metrics.json").write_text(text, encoding="utf-8")
    print(json.dumps({k: m[k] for k in ("pairs", "articles")}, indent=1)[:2500])


if __name__ == "__main__":
    main()
