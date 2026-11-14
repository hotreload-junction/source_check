"""Exa Agent vs our current evidence search, for attributed claims WITHOUT a link.

Manual experiment (real network + LLM + paid Exa calls), never part of tools/check.
    python -m experiments.exa_agent_compare CORPUS.json OUT.json [--efforts low,medium] [--workers 5] [--max-agent-usd 8]

Per claim:  one shared search plan (who / venue / period, from claim + paragraph), then
  current        our pipeline: planned queries + venue domain + exact quoted words (Exa Search), ranked, top 5
  agent_<effort> Exa Agent run (POST /agent/runs) asked for the original source (+ corroborating reports), top 5 URLs
EVERY URL of EVERY method is then fetched and judged by the SAME verify step (quote-first, rapidfuzz guard, occasion
and venue context, source_type), so the comparison is about what is VERIFIED, not about what a method claims.
"""
import argparse, json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import httpx
from app.cache import NullCache
from app.config import get_settings
from app.fetch import Fetcher
from app.fetch.urls import is_same_domain, registrable_domain
from app.llm import LLMClient
from app.pipeline import assess as A
from app.pipeline.evidence import Candidate, gather_candidates, is_venue_domain, plan_search
from app.pipeline.exa import NamedSourceHit
from app.pipeline.extract import ExtractedClaim
from app.pipeline.verify import fuzzy_score

S = get_settings()
LLM = LLMClient.from_settings(S, cache=NullCache())
FETCHER = Fetcher.from_settings(S, cache=NullCache())
AGENT_URL = "https://api.exa.ai/agent/runs"
HEAD = {"x-api-key": S.exa_api_key, "Content-Type": "application/json"}
SPEND = {"usd": 0.0}
SPEND_LOCK = threading.Lock()


class CostClient(httpx.Client):
    """httpx client that adds up Exa's own `costDollars` of every search response."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.cost, self.calls = 0.0, 0

    def post(self, *a, **k):
        r = super().post(*a, **k)
        self.calls += 1
        try:
            self.cost += float(r.json().get("costDollars", {}).get("total", 0))
        except Exception:
            pass
        return r


AGENT_PROMPT = """You are helping to fact-check a news article published on {date}. The article reports the statement below but gives no link to it.

CLAIM: {claim}

PARAGRAPH it appears in: {context}

Find where this statement was actually made or published. Prefer the ORIGINAL source: the interview transcript or video page of the outlet that is named, an official statement or press release, a report or document itself, a court, parliament or agency record, the speaker's own post. If the original cannot be found, find independent outlets that quote the same statement. Never return the article itself or other pages of {domain}. For every source give the URL, whether it is the original or only corroborating, and a VERBATIM quote of at most 300 characters copied from that page that shows the statement (null if you cannot quote it). Do not invent URLs or quotes. If nothing can be found, say so."""
SCHEMA = {"type": "object", "properties": {
    "found": {"type": "boolean"},
    "sources": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
        "url": {"type": "string"}, "kind": {"type": "string", "enum": ["original", "corroborating"]},
        "quote": {"type": ["string", "null"]}, "published_date": {"type": ["string", "null"]}}, "required": ["url", "kind"]}}},
    "required": ["found", "sources"]}


def agent_run(art, claim, effort, max_usd):
    with SPEND_LOCK:
        if SPEND["usd"] > max_usd:
            return {"error": "budget guard", "urls": [], "cost": 0, "secs": 0}
    t = time.time()
    body = {"query": AGENT_PROMPT.format(date=(art.get("published_at") or "recently")[:10], claim=claim["text"][:600],
                                         context=claim["context"][:1500], domain=art["domain"]),
            "effort": effort, "outputSchema": SCHEMA}
    for i in range(5):
        r = httpx.post(AGENT_URL, json=body, headers=HEAD, timeout=60)
        if r.status_code == 429:
            time.sleep(2 * (i + 1)); continue
        break
    if r.status_code != 200:
        return {"error": f"create http {r.status_code}: {r.text[:150]}", "urls": [], "cost": 0, "secs": time.time() - t}
    rid = r.json()["id"]
    while time.time() - t < 420:
        time.sleep(4)
        try:
            g = httpx.get(f"{AGENT_URL}/{rid}", headers=HEAD, timeout=30).json()
        except httpx.HTTPError:
            continue
        if g.get("status") in ("completed", "failed", "cancelled"):
            break
    cost = float((g.get("costDollars") or {}).get("total", 0) or 0)
    with SPEND_LOCK:
        SPEND["usd"] += cost
    out = g.get("output") or {}
    st = out.get("structured") or {}
    claimed = [{"url": x.get("url"), "kind": x.get("kind"), "quote": x.get("quote")} for x in (st.get("sources") or []) if x.get("url")]
    cites = [c["url"] for gr in out.get("grounding") or [] for c in gr.get("citations", []) if c.get("url")]
    return {"status": g.get("status"), "stop": g.get("stopReason"), "claimed": claimed, "citations": cites, "found": st.get("found"),
            "cost": cost, "secs": time.time() - t, "searches": (g.get("usage") or {}).get("searches"), "error": (g.get("error") or None)}


def judge(url, claim_obj, art, plan, cache, who, pub):
    """The common currency: fetch + verify one URL exactly like the pipeline would."""
    cand = Candidate(NamedSourceHit(url, None, None, who, 0), venue_hosted=is_venue_domain(url, plan))
    try:
        sa = A._assess_source(claim_obj, url, who, art["url"], cache, LLM, S, pub, plan=plan, cand=cand)
    except Exception as e:
        return {"url": url, "cat": "error", "err": str(e)[:80]}
    v = sa.verify
    cat = ("unreadable" if sa.fetch is None or sa.fetch.status != "ok" else "original" if sa.verdict == "supported"
           else "corroborating" if sa.corroborated else "no")
    return {"url": url, "cat": cat, "llm": v.llm_verdict if v else None, "stype": v.source_type if v else None,
            "fuzzy": v.fuzzy_score if v else None, "quote": (v.quote_original or "")[:200] if v else None,
            "venue": cand.venue_hosted, "fetch": sa.fetch.status if sa.fetch else None, "text": (sa.fetch.text or "") if sa.fetch else ""}


def run_claim(art, claim, efforts, max_usd, cache):
    pub = datetime.fromisoformat(art["published_at"].replace("Z", "+00:00")).replace(tzinfo=None) if art.get("published_at") else None
    who0 = claim["attributions"][0]
    plan = plan_search(who0, claim["text"], claim["context"], pub, llm=LLM, model=S.model_extract)
    who = plan.speaker or who0
    cobj = ExtractedClaim("c1", claim["text"], 0, len(claim["text"]), [], claim["attributions"], context=claim["context"])
    res = {"article": art["url"], "domain": art["domain"], "claim": claim["text"][:300], "who": who,
           "plan": {"venue": plan.venue, "medium": plan.medium, "from": str(plan.date_from), "to": str(plan.date_to),
                    "domains": plan.domains, "queries": plan.queries, "phrases": plan.phrases}, "arms": {}}

    def current():
        t = time.time(); ex = CostClient()
        cands = gather_candidates(plan, claim["text"], art["url"], pub, api_key=S.exa_api_key, exa_client=ex, step_cache=NullCache(), limit=5)
        urls = [c.hit.url for c in cands]
        return {"urls": urls, "cost": ex.cost, "searches": ex.calls, "secs_search": time.time() - t}

    def agent(effort):
        return agent_run(art, claim, effort, max_usd)

    with ThreadPoolExecutor(1 + len(efforts)) as pool:
        futs = {"current": pool.submit(current), **{f"agent_{e}": pool.submit(agent, e) for e in efforts}}
        raw = {k: f.result() for k, f in futs.items()}
    for arm, r in raw.items():
        if arm == "current":
            urls = r["urls"]
        else:
            claimed_urls = [c["url"] for c in r.get("claimed", [])]
            urls = [u for u in dict.fromkeys(claimed_urls + r.get("citations", [])) if not is_same_domain(u, art["url"])][:5]
        t = time.time()
        with ThreadPoolExecutor(5) as pool:
            judged = list(pool.map(lambda u: judge(u, cobj, art, plan, cache, who, pub), urls))
        r["judged"] = judged
        r["secs_verify"] = time.time() - t
        if arm != "current":                    # does the quote the agent hands over really occur on its page?
            by = {j["url"]: j for j in judged}
            for c in r.get("claimed", []):
                j = by.get(c["url"])
                c["quote_on_page"] = (fuzzy_score(c["quote"], j["text"]) if c.get("quote") and j and j.get("text") else None)
        for j in judged:
            j.pop("text", None)
        res["arms"][arm] = r
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus"); ap.add_argument("out")
    ap.add_argument("--efforts", default="low,medium"); ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--max-agent-usd", type=float, default=8.0); ap.add_argument("--limit-articles", type=int, default=0)
    a = ap.parse_args()
    corpus = json.load(open(a.corpus))
    if a.limit_articles:
        corpus = corpus[: a.limit_articles]
    efforts = [e for e in a.efforts.split(",") if e]
    jobs = [(art, c) for art in corpus for c in art["claims"]]
    print(f"{len(corpus)} articles, {len(jobs)} claims, efforts={efforts}, agent budget guard ${a.max_agent_usd}", flush=True)
    caches = {art["url"]: A._FetchCache(FETCHER, art["url"]) for art in corpus}
    results = []
    out = open(a.out, "w")
    with ThreadPoolExecutor(a.workers) as pool:
        futs = [pool.submit(run_claim, art, c, efforts, a.max_agent_usd, caches[art["url"]]) for art, c in jobs]
        for i, f in enumerate(futs):
            try:
                r = f.result()
            except Exception as e:
                print("claim failed:", type(e).__name__, str(e)[:100], flush=True); continue
            results.append(r)
            json.dump(results, open(a.out, "w"), ensure_ascii=False)
            print(f"[{i+1}/{len(jobs)}] {r['domain']:24} cur={[j['cat'][:4] for j in r['arms']['current']['judged']]} " +
                  " ".join(f"{k}={[j['cat'][:4] for j in v['judged']]}" for k, v in r['arms'].items() if k != 'current') +
                  f" | agent spend ${SPEND['usd']:.2f}", flush=True)
    print("DONE", f"agent spend ${SPEND['usd']:.2f}", flush=True)


if __name__ == "__main__":
    main()
