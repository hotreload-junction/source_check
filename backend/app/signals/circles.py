"""Citation-circle facts for depth 2 (M1): does a source's own citation lead back to the article or its operator?

Pure functions, no I/O. Whether the circle makes the `circle` signal fire is decided in `app/verdict.py`
(`circle_signal`), the one place for thresholds; this module only finds the path.

DECISIONS Q17: circle signal = a source leads back via citations to the article or its cluster.
A "citation" here is a link inside the passage a depth-1 source rests on (see pipeline/depth2.py). A circle is
closed when such a link points

- to the article's own registrable domain (the source cites the outlet that cites it), or
- to a domain in the article's operator cluster (same GA/AdSense ID as the article's domain).

Rules that protect against false positives:
- Only an EXACT domain match or an exact shared ID counts; "looks similar" never does.
- Bare homepages are skipped by the caller (credit / navigation links), not evidence of a citation.
- Nothing known about a domain (no HTML, no RDAP) means no cluster, so no circle.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Citation:
    """One depth-2 link: `parent_id` (a depth-1 source) cites `child_domain`."""
    claim_id: str            # a claim that cites the parent source: the path starts a -> claim -> parent
    parent_id: str           # depth-1 source id, e.g. "s1"
    child_id: str            # the cited source's id (a new depth-2 source, or an existing depth-1 one)
    child_domain: str


@dataclass(frozen=True)
class Circle:
    path: list[str]          # graph node ids, article first and last: ["a", "c1", "s1", "s3", "a"]
    closing_edge: tuple[str, str]   # (cited source node, "a"): the `cites_back` edge to draw
    kind: str                # "article_domain" | "article_cluster"


def article_cluster_domains(domains: list[dict], article_domain: str) -> set[str]:
    """Domains (other than the article's) that share the article domain's operator cluster."""
    cid = next((d["cluster_id"] for d in domains if d["domain"] == article_domain), None)
    if cid is None:
        return set()
    return {d["domain"] for d in domains if d["cluster_id"] == cid and d["domain"] != article_domain}


def find_circles(citations: list[Citation], article_domain: str, cluster_domains: set[str]) -> list[Circle]:
    """All closed circles, in the order of `citations` (stable, so a replayed analysis finds the same first one)."""
    circles: list[Circle] = []
    seen: set[tuple[str, ...]] = set()
    for c in citations:
        if c.child_domain == article_domain:
            kind = "article_domain"
        elif c.child_domain in cluster_domains:
            kind = "article_cluster"
        else:
            continue
        path = ["a", c.claim_id, c.parent_id, c.child_id, "a"]
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        circles.append(Circle(path, (c.child_id, "a"), kind))
    return circles


def circle_path(circles: list[Circle]) -> list[str] | None:
    """The path reported in `Signals.circle.path`: the first circle (None when there is none)."""
    return circles[0].path if circles else None
