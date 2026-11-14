"""B9: `GET /api/methodology` from eval/results/metrics.json (written by the eval harness, E4).

Never invents numbers: without a metrics file the answer is the contract shape with ZERO counts, the real thresholds
and the real models, which the frontend shows as "evaluation has not run yet" (all-zero confusion matrices)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, get_settings
from .verdict import thresholds

log = logging.getLogger("citetrace.methodology")
REPO_ROOT = Path(__file__).resolve().parents[2]
_ZERO = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}


def candidate_paths(settings: Settings) -> list[Path]:
    if settings.metrics_path:
        return [Path(settings.metrics_path)]
    return [REPO_ROOT / "eval" / "results" / "metrics.json", Path("/app/eval/results/metrics.json")]


def empty_methodology(settings: Settings) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verify_model": settings.model_verify, "prompt_version": settings.prompt_version,
        "pairs": {"n": 0, "n_cross_language": 0, "precision": 0.0, "recall": 0.0, "confusion": dict(_ZERO)},
        "articles": {"n_network": 0, "n_wire": 0, "fpr_wire": 0.0, "tpr_network": 0.0, "confusion": dict(_ZERO)},
        "thresholds": {**thresholds(), "fuzzy": settings.fuzzy_threshold},
        "tuned_on": "", "measured_on": "", "error_cases": [],
    }


def _valid(m: object) -> bool:
    if not isinstance(m, dict):
        return False
    try:
        return (all(k in m["pairs"] for k in ("n", "n_cross_language", "precision", "recall", "confusion"))
                and all(k in m["articles"] for k in ("n_network", "n_wire", "fpr_wire", "tpr_network", "confusion"))
                and all(k in m["pairs"]["confusion"] for k in _ZERO) and all(k in m["articles"]["confusion"] for k in _ZERO)
                and isinstance(m.get("error_cases", []), list))
    except (KeyError, TypeError):
        return False


def load_methodology(settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    for path in candidate_paths(s):
        if not path.is_file():
            continue
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("unreadable metrics file %s: %s", path, exc)
            continue
        if not _valid(m):
            log.warning("metrics file %s does not match the Methodology contract; showing the empty state", path)
            continue
        base = empty_methodology(s)
        out = {**base, **{k: v for k, v in m.items() if not k.startswith("_")}}
        # thresholds as MEASURED (from the file); the live code's values only fill keys the file does not have
        out["thresholds"] = {**base["thresholds"], **(m.get("thresholds") or {})}
        return out
    return empty_methodology(s)
