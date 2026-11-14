"""Add the arm `current_sec` (our search WITH plain news outlets verified, limit 8) to an existing comparison result.
Uses the stored plan of each claim, so planner variance is not part of the difference.   Real calls, manual only."""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from app.cache import NullCache
from experiments import exa_agent_compare as C
from app.pipeline import assess as A
from app.pipeline.evidence import SearchPlan, gather_candidates
from app.pipeline.extract import ExtractedClaim

corpus = {a["url"]: a for a in json.load(open(sys.argv[1]))}
R = json.load(open(sys.argv[2]))
caches = {u: A._FetchCache(C.FETCHER, u) for u in corpus}
claims = {(a["url"], c["text"][:300]): c for a in corpus.values() for c in a["claims"]}

def one(r):
    art = corpus[r["article"]]; claim = claims[(r["article"], r["claim"])]
    p = r["plan"]
    plan = SearchPlan(speaker=r["who"], venue=p["venue"], medium=p["medium"],
                      date_from=date.fromisoformat(p["from"]) if p["from"] != "None" else None,
                      date_to=date.fromisoformat(p["to"]) if p["to"] != "None" else None,
                      domains=p["domains"], queries=p["queries"], phrases=p["phrases"])
    pub = datetime.fromisoformat(art["published_at"].replace("Z", "+00:00")).replace(tzinfo=None) if art.get("published_at") else None
    cobj = ExtractedClaim("c1", claim["text"], 0, len(claim["text"]), [], claim["attributions"], context=claim["context"])
    t = time.time(); ex = C.CostClient()
    cands = gather_candidates(plan, claim["text"], art["url"], pub, api_key=C.S.exa_api_key, exa_client=ex, step_cache=NullCache(),
                              limit=8, include_secondary=True)
    urls = [c.hit.url for c in cands]
    with ThreadPoolExecutor(6) as pool:
        judged = list(pool.map(lambda u: C.judge(u, cobj, art, plan, caches[art["url"]], r["who"], pub), urls))
    for j in judged:
        j.pop("text", None)
    return {"urls": urls, "cost": ex.cost, "searches": ex.calls, "secs_search": time.time() - t, "judged": judged, "secs_verify": 0}

with ThreadPoolExecutor(5) as pool:
    for r, arm in zip(R, pool.map(one, R)):
        r["arms"]["current_sec"] = arm
        print(r["domain"], [j["cat"][:4] for j in arm["judged"]], flush=True)
json.dump(R, open(sys.argv[3], "w"), ensure_ascii=False)
