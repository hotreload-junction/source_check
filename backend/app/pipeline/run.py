"""B7: the whole depth-1 analysis for one article URL -> contract `Result` (contracts/api.md).

scrape -> extract claims -> (per claim: sources, fetch, verify) -> infra signals -> verdict.

`emit(event, data)` receives the contract SSE events (progress / node / edge / claim) as they become final, so the
API (B8) can stream them; the CLI uses the same hook for stderr progress. Nothing here calls the network except
through the injected fetcher / llm / rdap, which is what the tests replace.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit

from .. import cache as cachemod
from ..config import Settings, get_settings
from ..fetch import Fetcher
from ..fetch.urls import registrable_domain
from ..llm import LLMClient, LLMError
from ..signals import RdapClient, SourcePage, analyze_infrastructure
from ..signals.circles import article_cluster_domains, circle_path, find_circles
from ..signals.laundering import channel_sourced_claims, declared_reposts
from ..signals.networks import NetworkMatch, match_network
from ..signals.rdap import RdapResult
from ..signals.infra import PLATFORM_DOMAINS
from ..signals.reputation import ArchiveResult, WaybackClient, add_reputation
from ..signals.source_type import classify_source_type
from ..verdict import ClaimInput, decide
from .assess import ClaimAssessment, SourceAssessment, assess_claims
from .copies import find_article_copies
from .depth2 import expand_sources
from .extract import ExtractedClaim, extract_claims
from .textutil import article_body_filter, build_plain

log = logging.getLogger("citetrace.run")
Emit = Callable[[str, dict], None]


class AnalysisError(Exception):
    """The analysis cannot produce a result (article unreadable, LLM down). Shown to the user as `error`."""


@dataclass
class AnalysisOutput:
    result: dict
    assessments: list[ClaimAssessment]
    source_rows: list[dict] = field(default_factory=list)   # contract Source + the SourceAssessment behind it


class _CachedRdap:
    """RDAP lookups through the step cache: slow (rdap.org redirects to the registry, ~7 s measured) and a finished
    analysis must replay with the SAME ages. Only answers that contain a registration date are stored."""

    def __init__(self, inner, cache):
        self.inner, self.cache = inner, cache

    def lookup(self, domain: str) -> RdapResult:
        key = cachemod.make_key("rdap", registrable_domain(domain))
        hit = self.cache.get("rdap", key)
        if hit is not None:
            return RdapResult(**hit)
        res = self.inner.lookup(domain)
        if res.created_at is not None:
            self.cache.put("rdap", key, {"domain": res.domain, "created_at": res.created_at, "age_days": res.age_days, "error": res.error})
        return res


def _host(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def _label(text: str, n: int = 110) -> str:
    plain = build_plain(text).text
    return plain if len(plain) <= n else plain[: n - 1].rstrip() + "…"


class _CachedArchive:
    """Wayback first-capture lookups through the step cache (PLAN Q2): a finished analysis replays with the SAME dates.
    Answers are stored (also "never archived"); failures are not, so a later run can still get the date."""

    def __init__(self, inner, cache):
        self.inner, self.cache = inner, cache

    def lookup(self, domain: str) -> ArchiveResult:
        key = cachemod.make_key("wayback", registrable_domain(domain))
        hit = self.cache.get("wayback", key)
        if hit is not None:
            return ArchiveResult(**hit)
        res = self.inner.lookup(domain)
        if res.error is None:
            self.cache.put("wayback", key, {"domain": res.domain, "first_archived_at": res.first_archived_at, "error": None})
        return res


def _set_source_type(row: dict, html: str | None) -> None:
    """PLAN Q1: contract `source_type` + `source_type_reason` (context for the reader, never a verdict signal)."""
    st = classify_source_type(row.get("url"), html)
    row["source_type"] = st.type if st else None
    row["source_type_reason"] = st.reason if st else None


class _Registry:
    """Assigns s1, s2, ... once per cited URL (the same source for several claims is ONE node)."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._by_key: dict[str, dict] = {}

    def add(self, sa: SourceAssessment, anchor: str | None, kind: str | None = None, stub_key: str | None = None,
            depth: int = 1, parent: str | None = None) -> tuple[dict, bool]:
        key = sa.url if sa.url else (stub_key or f"stub:{len(self.rows)}")
        if key in self._by_key:
            return self._by_key[key], False
        f = sa.fetch
        row = {
            "id": f"s{len(self.rows) + 1}", "url": sa.url,
            "domain": registrable_domain(sa.url) if sa.url else None,
            "kind": kind or sa.kind, "attribution_text": anchor or sa.attribution_text, "depth": depth,
            "parent_source_id": parent,
            "fetch_status": f.status if f else "not_fetched", "fetched_via": f.via if f else None,
            "_html": f.html if f else None,
        }
        _set_source_type(row, row["_html"])
        self.rows.append(row)
        self._by_key[key] = row
        return row, True


def analyze(url: str, *, fetcher: Fetcher | None = None, llm: LLMClient | None = None, settings: Settings | None = None,
            rdap: RdapClient | None = None, emit: Emit | None = None, exa_client=None, archive=None,
            refresh: bool = False) -> AnalysisOutput:
    """`refresh`: ignore the step cache (re-fetch, re-ask every model, re-search) and store the new results."""
    s = settings or get_settings()
    sc = cachemod.for_run(refresh)
    fetcher = fetcher or Fetcher.from_settings(s, cache=sc)
    llm = llm or LLMClient.from_settings(s, cache=sc)
    rdap = _CachedRdap(rdap if rdap is not None else RdapClient(), sc)
    # Q2 Wayback first capture: `archive` injects a client (tests); WAYBACK_LOOKUP=false switches the live lookup off
    if archive is None and s.wayback_lookup:
        archive = WaybackClient(timeout=s.wayback_timeout_s)
    archive = _CachedArchive(archive, sc) if archive is not None else None
    out = emit or (lambda event, data: None)
    t0 = time.monotonic()

    def progress(step: str, message: str, pct: int) -> None:
        out("progress", {"step": step, "message": message, "pct": pct})

    # 1. scrape ---------------------------------------------------------------------------------------------
    progress("scrape_article", "Fetching article", 5)
    art = fetcher.fetch_article(url)
    if art.status != "ok" or not art.text:
        why = {"paywall": "is behind a paywall", "blocked": "blocks automated access",
               "unreachable": "could not be reached"}.get(art.status, f"could not be read ({art.status})")
        raise AnalysisError(f"The article {why}. Nothing to analyse.")
    article_host = _host(art.final_url or url) or _host(url) or ""
    article_domain = registrable_domain(article_host)
    published = datetime.fromisoformat(art.published_at.replace("Z", "+00:00")).replace(tzinfo=None) if art.published_at else None
    text_md = art.text
    out("node", {"id": "a", "type": "article", "label": art.title or article_host, "verdict": None, "cluster_id": None, "depth": 0})

    # 2. extract ---------------------------------------------------------------------------------------------
    progress("extract_claims", "Finding the central claims", 15)
    try:
        ex = extract_claims(text_md, url, llm=llm, settings=s, body_filter=article_body_filter(art.html))
    except LLMError as exc:
        raise AnalysisError(f"Claim extraction failed: {exc}") from exc
    claims = ex.claims
    for c in claims:
        out("node", {"id": c.id, "type": "claim", "label": _label(c.text), "verdict": None, "cluster_id": None, "depth": 0})
        out("edge", {"id": f"e-a-{c.id}", "source": "a", "target": c.id, "type": "makes_claim"})

    # RDAP does not depend on the verification: look the link domains up now, analyze_infrastructure hits the cache
    link_domains = sorted({registrable_domain(l.url) for c in claims for l in c.links} | {article_domain})
    rdap_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rdap")
    for d in link_domains:
        rdap_pool.submit(rdap.lookup, d)
        if archive is not None and d not in PLATFORM_DOMAINS:
            rdap_pool.submit(archive.lookup, d)

    # mirrors of the article on other domains: searched while the claims are verified, joined before the infrastructure step
    copies_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="copies")
    copies_future = copies_pool.submit(find_article_copies, url, text_md, api_key=s.exa_api_key, fetcher=fetcher,
                                       exa_client=exa_client, step_cache=sc) if (s.find_copies and s.exa_api_key) else None

    # 3. sources + verify ------------------------------------------------------------------------------------
    progress("fetch_sources", f"Checking the sources of {len(claims)} claims", 25)
    reg = _Registry()
    claim_dicts: list[dict] = [None] * len(claims)  # type: ignore[list-item]
    done = [0]

    def finalize(i: int, a: ClaimAssessment) -> None:
        cd = _claim_dict(a, reg, out, s.max_evidence_links)
        claim_dicts[i] = cd
        done[0] += 1
        out("claim", cd)
        out("node", {"id": cd["id"], "type": "claim", "label": _label(a.claim.text), "verdict": cd["verdict"],
                     "cluster_id": None, "depth": 0})
        progress("verify_claims", f"Verified {done[0]} of {len(claims)} claims", 25 + int(55 * done[0] / max(len(claims), 1)))

    assessments = assess_claims(claims, url, fetcher=fetcher, llm=llm, settings=s, exa_client=exa_client,
                                published_at=published, on_claim=finalize, step_cache=sc, article_text=text_md) if claims else []

    # 4. infrastructure --------------------------------------------------------------------------------------
    progress("infra_signals", "Looking at the infrastructure behind the sources", 85)
    rdap_pool.shutdown(wait=True)
    # Pages the search FOUND for an attributed claim are not sources the article cites: their infrastructure must not
    # feed the infrastructure signal (a shared ID between two search results would be a coincidence, not a finding).
    searched = _search_ids(reg.rows)
    pages = [SourcePage(domain=r["domain"], html=r.get("_html")) for r in reg.rows if r["domain"] and r["id"] not in searched]
    for r in reg.rows:
        r.pop("_html", None)
    if copies_future is not None:
        try:
            copies = copies_future.result(timeout=30)
        except Exception:      # slow or failed: no mirrors is the neutral answer
            copies = []
        pages += [SourcePage(domain=c.domain, html=c.html) for c in copies]
        if copies:
            progress("infra_signals", f"Found the same text on {len(copies)} other domains", 86)
    copies_pool.shutdown(wait=False)
    infra = analyze_infrastructure(article_host, art.html, pages, rdap.lookup)

    # 4b. depth 2 (M1, MAX_DEPTH>=2): what the sources themselves cite; a link back to the article = citation circle
    depth = 1
    d2_edges: list[dict] = []
    circles = []
    if s.max_depth >= 2 and reg.rows:
        progress("depth2", "Following the sources of the sources", 88)
        d2 = expand_sources(reg.rows, assessments, claim_dicts, fetcher=fetcher, article_url=url,
                            article_domain=article_domain, emit=out)
        depth = 2
        # what a search-found page cites is not cited by the article either: shown in the graph, but no signal
        searched = _search_ids(reg.rows, d2.citations)
        signal_pages = [p for r, p in zip(d2.rows, d2.pages) if r["id"] not in searched]
        signal_cites = [c for c in d2.citations if c.parent_id not in searched]
        if signal_pages:     # the new domains join the infrastructure analysis (shared IDs, ages) and the clusters
            infra = analyze_infrastructure(article_host, art.html, pages + signal_pages, rdap.lookup)
        for r, p in zip(d2.rows, d2.pages):
            _set_source_type(r, p.html)
        d2_edges = d2.edges
        circles = find_circles(signal_cites, article_domain, article_cluster_domains(infra.domains, article_domain))
        back = {c.closing_edge for c in circles}      # one closing edge per cited source, however many paths reach it
        d2_edges += [{"id": f"e-{src}-back", "source": src, "target": dst, "type": "cites_back"} for src, dst in sorted(back)]
    domain_cluster = {d["domain"]: d["cluster_id"] for d in infra.domains}
    # Q2 reputation badges (context only, never read by verdict.py): WP:RSP + Tranco snapshots, Wayback first capture
    add_reputation(infra.domains, archive.lookup if archive is not None else None, skip=PLATFORM_DOMAINS)

    # 5. verdict ---------------------------------------------------------------------------------------------
    progress("verdict", "Deciding", 95)
    # documented networks (published lists) for the article and every source the article (or a source) cites
    net_reasons = _network_reasons(art.final_url or url, url, reg.rows, infra.domains, searched)
    channel_ids, linked = channel_sourced_claims(claim_dicts, reg.rows)
    decision = decide([ClaimInput(c["id"], c["verdict"], c["verdict"] == "supported" and not c["source_ids"] and bool(c.get("found_source_ids")))
                       for c in claim_dicts], infra.domains, infra.shared_id_reasons(),
                      circle_path(circles), network_reasons=net_reasons,
                      declared_reposts=[d.line for d in declared_reposts(text_md)],
                      channel_claim_ids=channel_ids, linked_claims=linked)
    nodes, edges = _graph(art.title or article_host, article_domain, claim_dicts, reg.rows, infra, domain_cluster, d2_edges, searched)
    for n in nodes:
        if n["type"] in ("domain", "source"):
            out("node", n)
    for e in edges:
        if e["type"] in ("hosted_on", "shares_infra", "cites_back"):
            out("edge", e)

    result = {
        "article": {"url": url, "domain": article_host, "title": art.title, "published_at": art.published_at,
                    "text_markdown": text_md},
        "claims": claim_dicts,
        "sources": reg.rows,
        "domains": infra.domains,
        "graph": {"nodes": nodes, "edges": edges},
        "signals": decision["signals"],
        "verdict": decision["verdict"],
        "audit": {"model_extract": s.model_extract, "model_verify": s.model_verify, "prompt_version": s.prompt_version,
                  "fuzzy_threshold": s.fuzzy_threshold, "depth": depth, "duration_s": round(time.monotonic() - t0, 1)},
    }
    progress("verdict", "Done", 100)
    return AnalysisOutput(result, assessments, reg.rows)


def _network_reasons(article_final_url: str, article_url: str, rows: list[dict], domains: list[dict],
                     searched: set[str] = frozenset()) -> list[str]:
    """Plain-English reasons for hosts on a published network list; sets `known_network` on the matching Domain[].
    Pages a SEARCH found for an attributed claim are not the article's citations and are skipped."""
    hits: list[tuple[str, NetworkMatch]] = []
    art = match_network(article_final_url) or match_network(article_url)
    if art:
        hits.append(("the article", art))
    seen = {art.host} if art else set()
    for r in rows:
        if not r.get("url") or r["id"] in searched or r.get("kind") == "self":   # own-site links say nothing new
            continue
        m = match_network(r["url"])
        if m and m.host not in seen:
            seen.add(m.host)
            hits.append(("a cited source" if r["depth"] == 1 else "a source of a source", m))
    for d in domains:
        d.setdefault("known_network", None)
    reasons = []
    for role, m in hits:
        reasons.append(f"{m.host} ({role}) is on the published list of the {m.network} ({m.list_source})")
        dom = registrable_domain(m.host)
        for d in domains:
            if d["domain"] in (dom, m.host):
                d["known_network"] = m.network
    return reasons


def _search_ids(rows: list[dict], citations=()) -> set[str]:
    """Ids of the pages the SEARCH found for an attributed claim (kind named WITH a url; the unresolved person stub has
    none) and of the pages below them. The article cites neither, so they never feed the infrastructure, network or
    circle signals. A page below one is only kept in the signals when a source the article does cite leads there too."""
    found = {r["id"] for r in rows if r["kind"] == "named" and r["url"]}
    cited_elsewhere = {c.child_id for c in citations if c.parent_id not in found}
    return found | {r["id"] for r in rows if r.get("parent_source_id") in found and r["id"] not in cited_elsewhere}


def _claim_dict(a: ClaimAssessment, reg: _Registry, out: Emit, max_links: int = 2) -> dict:
    """Contract `Claim`; registers the sources this claim cites (and emits their graph pieces).

    Attributed claim ("Person X said ..." without a link): the person is only the attribution, not a source. The
    pages the search found for the statement ARE the claim's sources (depth 1, `attribution_text` = who said it); what
    those pages cite follows as depth 2 (M1). Nothing found: one stub Source (the named person, no URL) says so.
    `evidence.source_id` points at the found page that carries the quote, so the UI shows the verdict on that link."""
    c = a.claim
    source_ids: list[str] = []
    found_ids: list[str] = []        # unsourced claim: pages the search found (the article cites none: source_ids stays empty)
    anchors = {l.url: l.anchor_text for l in c.links}

    def node_event(row: dict) -> None:
        out("node", {"id": row["id"], "type": "source", "label": row["domain"] or row["attribution_text"] or "unknown",
                     "verdict": None, "cluster_id": None, "depth": row["depth"]})

    def cite(sa: SourceAssessment, anchor: str | None, kind: str | None = None, stub_key: str | None = None,
             depth: int = 1, parent: dict | None = None, into: list | None = None) -> dict:
        row, new = reg.add(sa, anchor, kind, stub_key, depth, parent["id"] if parent else None)
        target = source_ids if into is None else into
        if row["id"] not in target:
            target.append(row["id"])
        if new:
            node_event(row)
        out("edge", {"id": f"e-{c.id}-{row['id']}", "source": c.id, "target": row["id"], "type": "cites"})
        if parent is not None:
            out("edge", {"id": f"e-{parent['id']}-{row['id']}", "source": parent["id"], "target": row["id"], "type": "cites"})
        return row

    evidence_row = None
    if c.links:
        by_url: dict[str, dict] = {}
        for sa in a.sources:
            if sa.depth == 2 and sa.parent_url in by_url:         # source of a self-cited page: below it, not next to the claim
                cite(sa, None, depth=2, parent=by_url[sa.parent_url])
            else:
                by_url[sa.url] = cite(sa, anchors.get(sa.url))
    elif not c.attributions:
        for sa in a.sources:                                         # unsourced claim, supporting pages found by search
            cite(sa, None, "named", into=found_ids)
    elif c.attributions:
        who = (a.plan.speaker if a.plan and a.plan.speaker else c.attributions[0]).strip()
        found = [sa for sa in a.sources if sa.verdict == "supported" or sa.corroborated]
        found.sort(key=lambda sa: (sa.verdict != "supported", not sa.venue_hosted, -(sa.verify.fuzzy_score or 0) if sa.verify else 0))
        for sa in found[:max_links]:
            row = cite(sa, who, "named")
            if a.best is not None and sa.url == a.best.url:
                evidence_row = row
        if not found:
            # one node per attributed party: three claims "according to X" cite the same X, not three sources
            cite(SourceAssessment(None, "named", who, None, None, "not_checkable"), who, "named", stub_key=f"stub:{who.lower()}")

    ev = a.evidence
    evidence = None
    if ev is not None and a.best is not None and a.best.url:
        evidence = {"source_id": (evidence_row or next(r for r in reg.rows if r["url"] == a.best.url))["id"],
                    "quote_original": ev.quote_original, "quote_translation": ev.quote_translation,
                    "language": ev.language or "und", "fuzzy_score": round(ev.fuzzy_score or 0.0, 1)}
    return {"id": c.id, "text": c.text, "char_start": c.char_start, "char_end": c.char_end, "source_ids": source_ids,
            "found_source_ids": found_ids,
            "verdict": a.verdict, "subtype": a.subtype, "evidence": evidence, "explanation": a.explanation}


def _graph(title: str, article_domain: str, claims: list[dict], sources: list[dict], infra, domain_cluster: dict,
           depth2_edges: list[dict] = (), searched: set[str] = frozenset()) -> tuple[list, list]:
    nodes = [{"id": "a", "type": "article", "label": title, "verdict": None,
              "cluster_id": domain_cluster.get(article_domain), "depth": 0}]
    edges: list[dict] = []
    for c in claims:
        nodes.append({"id": c["id"], "type": "claim", "label": _label(c["text"]), "verdict": c["verdict"], "cluster_id": None, "depth": 0})
        edges.append({"id": f"e-a-{c['id']}", "source": "a", "target": c["id"], "type": "makes_claim"})
        for sid in c["source_ids"]:
            edges.append({"id": f"e-{c['id']}-{sid}", "source": c["id"], "target": sid, "type": "cites"})
    for sr in sources:
        nodes.append({"id": sr["id"], "type": "source", "label": sr["domain"] or sr["attribution_text"] or "unknown",
                      "verdict": None, "cluster_id": domain_cluster.get(sr["domain"]), "depth": sr["depth"]})
        if sr["domain"] and sr["id"] not in searched:     # search-found pages have no domain node (not cited by the article)
            edges.append({"id": f"e-{sr['id']}-host", "source": sr["id"], "target": f"d:{sr['domain']}", "type": "hosted_on"})
        if sr["parent_source_id"]:                                  # page -> the sources it cites (depth 2: found page, self-cited page)
            edges.append({"id": f"e-{sr['parent_source_id']}-{sr['id']}", "source": sr["parent_source_id"], "target": sr["id"], "type": "cites"})
    for c in claims:                                                # claim -> pages found for it (unsourced claims)
        for sid in c.get("found_source_ids", []):
            edges.append({"id": f"e-{c['id']}-{sid}", "source": c["id"], "target": sid, "type": "cites"})
    first_depth: dict[str, int] = {}      # a domain is as deep as the shallowest source hosted on it
    for sr in sources:
        if sr["domain"] and sr["id"] not in searched:
            first_depth[sr["domain"]] = min(first_depth.get(sr["domain"], 2), sr["depth"])
    for d in infra.domains:
        nodes.append({"id": f"d:{d['domain']}", "type": "domain", "label": d["domain"], "verdict": None,
                      "cluster_id": d["cluster_id"], "depth": 0 if d["is_article_domain"] else first_depth.get(d["domain"], 1)})
        if d["is_article_domain"]:
            edges.append({"id": "e-a-host", "source": "a", "target": f"d:{d['domain']}", "type": "hosted_on"})
    edges += depth2_edges
    edges += infra.edges
    seen, unique = set(), []
    for e in edges:                      # the same link can be produced by the claim step and by M1's expansion
        key = (e["source"], e["target"], e["type"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return nodes, unique
