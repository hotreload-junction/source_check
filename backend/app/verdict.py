"""Article verdict: the two-signal rule (DECISIONS Q17). The ONE place for detection thresholds.

Pure functions, no I/O, no LLM. Inputs are facts from earlier steps; output matches contract `Signals`
and `Result.verdict`.

Signals: evidence (claims) / infrastructure (shared IDs, young source domains, documented network) / circle (depth 2)
/ laundering (the article republishes Telegram/social channel posts).

Rule, in this order:
1. At least 2 different signals that do NOT depend on claim verdicts fired (infrastructure / circle / laundering)
   -> `propaganda_pattern`, even when the claims themselves could not be checked (those facts stand on their own).
2. No claims, or more than half of the claims `not_checkable` -> `insufficient_evidence`.
3. At least 2 different signals fired -> `propaganda_pattern`.
4. Only the evidence signal fired -> `weakly_sourced`.
5. Otherwise -> `well_sourced`.

Invariants (AGENTS.md section 7):
- `not_checkable` (unreachable, paywall, social_media, self_citation) never counts against an article.
- One bad claim can never produce `propaganda_pattern`: the evidence signal needs MIN_BAD_CLAIMS claims.
- Unknown domain age (None) is never "young". A host that is not on a published network list says nothing.
- Faithfully copying a channel post makes a claim "supported" by it; the laundering signal records the copying.
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
MIN_CHANNEL_CLAIMS = 2        # laundering signal: >= this many claims whose only cited links are Telegram/social posts,
CHANNEL_MAJORITY = 0.5        # ... and they are MORE than this share of the claims that cite any link at all

BAD_CLAIM_VERDICTS = frozenset({"distorted", "contradicted", "not_mentioned", "unsourced"})


def thresholds() -> dict[str, float]:
    """For the methodology page (`Methodology.thresholds`)."""
    return {
        "min_bad_claims": MIN_BAD_CLAIMS,
        "young_domain_days": YOUNG_DOMAIN_DAYS,
        "insufficient_ratio": INSUFFICIENT_RATIO,
        "min_signals_for_pattern": MIN_SIGNALS_FOR_PATTERN,
        "min_channel_claims": MIN_CHANNEL_CLAIMS,
        "channel_majority": CHANNEL_MAJORITY,
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


def infrastructure_signal(domains: Sequence[dict], shared_id_reasons: Sequence[str],
                          network_reasons: Sequence[str] = ()) -> dict:
    """Fires on a shared analytics/AdSense ID (reasons come from `signals.InfraResult`), on a SOURCE
    domain younger than YOUNG_DOMAIN_DAYS, or on the article / a cited source being on a published list of a
    documented influence network (`signals/networks.py`). The article's own domain AGE is not part of the rule."""
    reasons = list(network_reasons) + list(shared_id_reasons)
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


def laundering_signal(declared: Sequence[str] = (), channel_claim_ids: Sequence[str] = (), linked_claims: int = 0) -> dict:
    """Fires when the page declares a Telegram channel as its source, or when at least MIN_CHANNEL_CLAIMS claims rest
    only on Telegram/social posts AND they are the majority of the claims that cite any link."""
    reasons = [f'The page names a Telegram channel as its source: "{d}"' for d in declared]
    many = len(channel_claim_ids) >= MIN_CHANNEL_CLAIMS and len(channel_claim_ids) > CHANNEL_MAJORITY * max(linked_claims, 1)
    if many:
        reasons.append(f"{len(channel_claim_ids)} of {linked_claims} linked claims rest only on Telegram/social posts "
                       f"(threshold: {MIN_CHANNEL_CLAIMS} and more than {CHANNEL_MAJORITY:.0%}).")
    return {"fired": bool(reasons), "claim_ids": list(channel_claim_ids) if reasons else [], "reasons": reasons}


def decide(claims: Sequence[ClaimInput], domains: Sequence[dict] = (),
           shared_id_reasons: Sequence[str] = (), circle_path: Sequence[str] | None = None,
           network_reasons: Sequence[str] = (), declared_reposts: Sequence[str] = (),
           channel_claim_ids: Sequence[str] = (), linked_claims: int = 0) -> dict:
    """-> {"signals": Signals, "verdict": {"label", "summary"}}."""
    signals = {
        "evidence": evidence_signal(claims),
        "infrastructure": infrastructure_signal(domains, shared_id_reasons, network_reasons),
        "circle": circle_signal(circle_path),
        "laundering": laundering_signal(declared_reposts, channel_claim_ids, linked_claims),
    }
    return {"signals": signals, "verdict": _label(claims, signals)}


SIGNAL_ORDER = ("evidence", "infrastructure", "circle", "laundering")
CLAIM_INDEPENDENT = ("infrastructure", "circle", "laundering")
_NAMES = {"evidence": "weak claim support", "infrastructure": "shared, very young or documented-network infrastructure",
          "circle": "a citation circle", "laundering": "content republished from Telegram/social channels"}


def _pattern(fired: list[str]) -> dict:
    return {"label": "propaganda_pattern",
            "summary": "Independent signals agree: " + " and ".join(_NAMES[k] for k in fired) + "."}


def _label(claims: Sequence[ClaimInput], signals: dict) -> dict:
    fired = [k for k in SIGNAL_ORDER if signals[k]["fired"]]
    independent = [k for k in fired if k in CLAIM_INDEPENDENT]
    if len(independent) >= MIN_SIGNALS_FOR_PATTERN:   # facts about infrastructure / provenance, not about claims
        return _pattern(fired)

    n = len(claims)
    unchecked = sum(1 for c in claims if c.verdict == "not_checkable")
    if n == 0 or unchecked / n > INSUFFICIENT_RATIO:
        why = "No central claims were found." if n == 0 else (
            f"{unchecked} of {n} central claims could not be checked (source not locatable, "
            "unreachable, paywalled, social media or self-citation)."
        )
        return {"label": "insufficient_evidence",
                "summary": f"{why} Not enough evidence to judge; this does not count against the article."}

    if len(fired) >= MIN_SIGNALS_FOR_PATTERN:
        return _pattern(fired)
    if fired == ["evidence"]:
        return {"label": "weakly_sourced",
                "summary": "Several central claims are unsupported by their sources or unsourced, "
                           "but no sign of coordinated infrastructure was found."}
    if fired:
        return {"label": "well_sourced",
                "summary": f"One warning signal fired ({_NAMES[fired[0]]}), but a single signal is not enough for a "
                           "propaganda pattern; the checkable claims hold up."}
    return {"label": "well_sourced",
            "summary": "No two independent warning signals fired; the checkable claims hold up."}
