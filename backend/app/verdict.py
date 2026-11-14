"""Article verdict: the two-signal rule (DECISIONS Q17). The ONE place for detection thresholds.

Pure functions, no I/O, no LLM. Inputs are facts from earlier steps; output matches contract `Signals`
and `Result.verdict`.

Rule, in this order:
1. No claims, or more than half of the claims `not_checkable` -> `insufficient_evidence`.
2. At least 2 different signals fired (evidence / infrastructure / circle) -> `propaganda_pattern`.
3. Only the evidence signal fired -> `weakly_sourced`.
4. Otherwise -> `well_sourced`.

Invariants (AGENTS.md section 7):
- `not_checkable` (unreachable, paywall, social_media, self_citation) never counts against an article.
- One bad claim can never produce `propaganda_pattern`: the evidence signal needs MIN_BAD_CLAIMS claims.
- Unknown domain age (None) is never "young".
- Start values are tuned only on eval/ data, never on the demo URLs. Report changes on the board.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# ---- thresholds (the only place) ---------------------------------------------------------------
MIN_BAD_CLAIMS = 2            # evidence signal: >= this many central claims with a bad verdict
YOUNG_DOMAIN_DAYS = 90        # infra signal: a source domain younger than this many days
INSUFFICIENT_RATIO = 0.5      # more than this share of claims not_checkable -> insufficient_evidence
MIN_SIGNALS_FOR_PATTERN = 2   # different signals needed for propaganda_pattern

BAD_CLAIM_VERDICTS = frozenset({"distorted", "contradicted", "not_mentioned", "unsourced"})


def thresholds() -> dict[str, float]:
    """For the methodology page (`Methodology.thresholds`)."""
    return {
        "min_bad_claims": MIN_BAD_CLAIMS,
        "young_domain_days": YOUNG_DOMAIN_DAYS,
        "insufficient_ratio": INSUFFICIENT_RATIO,
        "min_signals_for_pattern": MIN_SIGNALS_FOR_PATTERN,
    }


@dataclass(frozen=True)
class ClaimInput:
    id: str
    verdict: str  # contract ClaimVerdict


def evidence_signal(claims: Sequence[ClaimInput]) -> dict:
    bad = [c.id for c in claims if c.verdict in BAD_CLAIM_VERDICTS]
    fired = len(bad) >= MIN_BAD_CLAIMS
    detail = (
        f"{len(bad)} of {len(claims)} central claims are distorted, contradicted, not mentioned "
        f"in their source or unsourced (threshold: {MIN_BAD_CLAIMS})."
    )
    return {"fired": fired, "claim_ids": bad if fired else [], "detail": detail}


def infrastructure_signal(domains: Sequence[dict], shared_id_reasons: Sequence[str]) -> dict:
    """Fires on a shared analytics/AdSense ID (reasons come from `signals.InfraResult`) or on a SOURCE
    domain younger than YOUNG_DOMAIN_DAYS. The article's own domain age is not part of the rule."""
    reasons = list(shared_id_reasons)
    for d in domains:
        age = d.get("age_days")
        if d.get("is_article_domain") or age is None:
            continue
        if age < YOUNG_DOMAIN_DAYS:
            reasons.append(
                f"{d['domain']} was registered {age} days ago (threshold: {YOUNG_DOMAIN_DAYS})"
            )
    return {"fired": bool(reasons), "reasons": reasons}


def circle_signal(circle_path: Sequence[str] | None) -> dict:
    """Depth 2 only: a source leads back via citations to the article or its cluster."""
    return {"fired": bool(circle_path), "path": list(circle_path) if circle_path else None}


def decide(claims: Sequence[ClaimInput], domains: Sequence[dict] = (),
           shared_id_reasons: Sequence[str] = (), circle_path: Sequence[str] | None = None) -> dict:
    """-> {"signals": Signals, "verdict": {"label", "summary"}}."""
    signals = {
        "evidence": evidence_signal(claims),
        "infrastructure": infrastructure_signal(domains, shared_id_reasons),
        "circle": circle_signal(circle_path),
    }
    return {"signals": signals, "verdict": _label(claims, signals)}


def _label(claims: Sequence[ClaimInput], signals: dict) -> dict:
    n = len(claims)
    unchecked = sum(1 for c in claims if c.verdict == "not_checkable")
    if n == 0 or unchecked / n > INSUFFICIENT_RATIO:
        why = "No central claims were found." if n == 0 else (
            f"{unchecked} of {n} central claims could not be checked (source not locatable, "
            "unreachable, paywalled, social media or self-citation)."
        )
        return {"label": "insufficient_evidence",
                "summary": f"{why} Not enough evidence to judge; this does not count against the article."}

    fired = [k for k in ("evidence", "infrastructure", "circle") if signals[k]["fired"]]
    if len(fired) >= MIN_SIGNALS_FOR_PATTERN:
        names = {"evidence": "weak claim support", "infrastructure": "shared or very young source infrastructure",
                 "circle": "a citation circle"}
        return {"label": "propaganda_pattern",
                "summary": "Independent signals agree: " + " and ".join(names[k] for k in fired) + "."}
    if fired == ["evidence"]:
        return {"label": "weakly_sourced",
                "summary": "Several central claims are unsupported by their sources or unsourced, "
                           "but no sign of coordinated infrastructure was found."}
    return {"label": "well_sourced",
            "summary": "No two independent warning signals fired; the checkable claims hold up."}
