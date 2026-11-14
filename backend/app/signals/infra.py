"""Infrastructure facts for one analysis (B5): per-domain IDs + age, operator clusters, graph edges.

This module reports FACTS only. Whether they make the infrastructure signal fire (shared ID, domain
younger than 90 days) is decided in `app/verdict.py`, the one place for thresholds (AGENTS.md section 7).

Rules that protect against false positives:
- Social platforms / Telegram are never fetched for IDs or age: their infra says nothing about the cited
  claim. They still appear as domains (no data, neutral).
- A source on the article's own domain is a self-citation, not a separate operator: it is skipped.
- Unknown (no HTML, no RDAP answer) is None / empty, never a signal.
- Clusters need an EXACT shared ID. Cluster ids are `k1`, `k2`, ... in domain order.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ..fetch.urls import SOCIAL_DOMAINS, registrable_domain
from .html_ids import extract_ids
from .rdap import RdapResult

PLATFORM_DOMAINS = SOCIAL_DOMAINS | {"t.me", "telegram.me"}


@dataclass
class SourcePage:
    """A cited source as seen by the infra step. `html` is the raw page, None if it was not fetched."""
    domain: str
    html: str | None = None


@dataclass
class SharedId:
    id: str
    domains: list[str]


@dataclass
class InfraResult:
    domains: list[dict]                      # contract `Domain[]`
    edges: list[dict]                        # `shares_infra` GraphEdges between domain nodes (`d:<domain>`)
    shared_ids: list[SharedId] = field(default_factory=list)

    @property
    def has_shared_infra(self) -> bool:
        """>= 1 ID shared by two or more domains. Every such group holds at least one source domain,
        because the article has only one domain."""
        return bool(self.shared_ids)

    def shared_id_reasons(self) -> list[str]:
        """Plain-English reasons for the signal, e.g. "a.com and b.net share G-ABC123"."""
        return [f"{_join(s.domains)} share {s.id}" for s in self.shared_ids]


def _join(names: list[str]) -> str:
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def analyze_infrastructure(
    article_domain: str,
    article_html: str | None,
    sources: Iterable[SourcePage],
    rdap: Callable[[str], RdapResult] | None = None,
    *,
    max_workers: int = 4,
) -> InfraResult:
    """Build `Domain[]`, clusters and `shares_infra` edges. `rdap=None` disables age lookups (age stays null)."""
    art = registrable_domain(article_domain)
    pages: dict[str, str | None] = {art: article_html}
    for s in sources:
        dom = registrable_domain(s.domain)
        if dom == art:
            continue  # self-citation
        if pages.get(dom) is None:  # several URLs on one domain: keep any HTML we have
            pages[dom] = s.html

    ages: dict[str, RdapResult] = {}
    lookup = [d for d in pages if rdap and d not in PLATFORM_DOMAINS]
    if lookup:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(lookup)))) as pool:
            for d, res in zip(lookup, pool.map(_safe(rdap), lookup)):
                ages[d] = res

    domains: list[dict] = []
    for d, html in pages.items():
        ids = extract_ids(html) if d not in PLATFORM_DOMAINS else extract_ids(None)
        age = ages.get(d)
        domains.append({
            "domain": d,
            "created_at": age.created_at if age else None,
            "age_days": age.age_days if age else None,
            "analytics_ids": ids.analytics_ids,
            "adsense_ids": ids.adsense_ids,
            "cluster_id": None,
            "is_article_domain": d == art,
            "known_network": None,         # set by the pipeline from signals/networks.py (published lists)
        })

    shared = _shared_ids(domains)
    _assign_clusters(domains, shared)
    return InfraResult(domains=domains, edges=_edges(domains), shared_ids=shared)


def _safe(rdap: Callable[[str], RdapResult]) -> Callable[[str], RdapResult]:
    def run(domain: str) -> RdapResult:
        try:
            return rdap(domain)
        except Exception as exc:  # never let a lookup break the analysis
            return RdapResult(domain, None, None, error=type(exc).__name__)
    return run


def _shared_ids(domains: list[dict]) -> list[SharedId]:
    by_id: dict[str, list[str]] = {}
    for d in domains:
        for i in (*d["analytics_ids"], *d["adsense_ids"]):
            by_id.setdefault(i, []).append(d["domain"])
    return [SharedId(i, ds) for i, ds in by_id.items() if len(ds) >= 2]


def _assign_clusters(domains: list[dict], shared: list[SharedId]) -> None:
    """Union-find over shared IDs: A~B via X and B~C via Y puts A, B, C in one cluster."""
    parent = {d["domain"]: d["domain"] for d in domains}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in shared:
        root = find(s.domains[0])
        for other in s.domains[1:]:
            parent[find(other)] = root

    members: dict[str, list[str]] = {}
    for d in domains:
        members.setdefault(find(d["domain"]), []).append(d["domain"])
    names: dict[str, str] = {}
    for d in domains:  # number clusters in domain order
        root = find(d["domain"])
        if len(members[root]) >= 2:
            names.setdefault(root, f"k{len(names) + 1}")
            d["cluster_id"] = names[root]


def _edges(domains: list[dict]) -> list[dict]:
    """Star per cluster (first member -> others): enough to draw the cluster, no n^2 hairball."""
    by_cluster: dict[str, list[str]] = {}
    for d in domains:
        if d["cluster_id"]:
            by_cluster.setdefault(d["cluster_id"], []).append(d["domain"])
    edges: list[dict] = []
    for members in by_cluster.values():
        for other in members[1:]:
            edges.append({
                "id": f"e-infra-{len(edges) + 1}",
                "source": f"d:{members[0]}",
                "target": f"d:{other}",
                "type": "shares_infra",
            })
    return edges
