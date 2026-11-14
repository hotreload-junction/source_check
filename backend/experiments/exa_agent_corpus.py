"""Build the comparison corpus: >= 12 articles from different outlets, each with up to 3 ATTRIBUTED claims that have no link.

Manual experiment (real network + LLM calls), never part of tools/check.   python -m experiments.exa_agent_corpus OUT.json
Discovery: a few Exa news searches for "X said / told reporters / in a statement" pages from the last week + fixed seeds.
"""
import json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
import httpx
from app.config import get_settings
from app.fetch import Fetcher
from app.fetch.urls import registrable_domain
from app.llm import LLMClient
from app.pipeline.extract import extract_claims

s = get_settings().model_copy(update={"extract_votes": 3})      # corpus building only: cheaper than production K=5
f, llm = Fetcher.from_settings(s), LLMClient.from_settings(s)
SEEDS = [
    "https://rt-english.com/news/648799-huang-nvidia-downfall-explainer/",
    "https://apnews.com/article/russia-ukraine-war-nato-us-zelenskyy-f9a8629626c087424f05adc85d127780",
    "https://www.dw.com/en/ukraine-zelenskyy-says-security-deal-could-go-to-referendum/a-79947535",
    "https://www.aljazeera.com/news/2026/11/14/south-sudan-postpones-first-ever-national-elections-until-february",
    "https://tass.com/politics/2218183",
    "https://www.bbc.com/news/articles/cwvg2l312ylqo",
]
QUERIES = ["minister said in an interview this week", "told reporters on Thursday that", "CEO said in a statement on Wednesday",
           "according to a report published this week the commission said", "spokesperson said at a press briefing", "senator said on Sunday television interview"]

def post_retry(url, **kw):
    for i in range(4):
        try:
            return httpx.post(url, timeout=60, **kw)
        except httpx.HTTPError:
            time.sleep(2 * (i + 1))
    raise RuntimeError("network")


def discover():
    urls = list(SEEDS)
    for q in QUERIES:
        r = post_retry("https://api.exa.ai/search", json={"query": q, "numResults": 10, "type": "auto", "category": "news",
                       "startPublishedDate": "2026-11-08T00:00:00.000Z", "endPublishedDate": "2026-11-14T23:59:59.000Z"},
                       headers={"x-api-key": s.exa_api_key})
        urls += [x["url"] for x in r.json().get("results", [])]
    seen, out = {}, []
    for u in urls:
        d = registrable_domain(u)
        if seen.get(d, 0) >= 1 or any(k in u for k in ("/video", "youtube.com", "/live", "liveblog")):
            continue
        seen[d] = seen.get(d, 0) + 1
        out.append(u)
    return out

def prepare(u):
    a = f.fetch_article(u)
    if a.status != "ok" or not a.text or len(a.text) < 1500:
        return None
    try:
        ex = extract_claims(a.text, u, llm=llm, settings=s)
    except Exception as e:
        return None
    claims = [{"text": c.text, "context": c.context, "attributions": c.attributions} for c in ex.claims
              if c.attributions and not c.links and len(c.text) >= 60]
    if not claims:
        return None
    return {"url": u, "domain": registrable_domain(u), "title": a.title, "published_at": a.published_at, "claims": claims[:3]}

if __name__ == "__main__":
    urls = discover()
    print(len(urls), "candidate articles", flush=True)
    with ThreadPoolExecutor(6) as p:
        res = [r for r in p.map(prepare, urls) if r]
    print(len(res), "articles with >=1 attributed unlinked claim,", sum(len(r["claims"]) for r in res), "claims", flush=True)
    json.dump(res, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
    for r in res:
        print(f"  {r['domain']:28} {len(r['claims'])} claims | {r['title'][:60] if r['title'] else ''}")
