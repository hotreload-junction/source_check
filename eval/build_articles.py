"""Build eval/data/articles.csv: article URLs labelled by PROVENANCE (no reading, no judgement of our own).

- network (positive): English-language outlets of the "Pravda" network (Portal Kombat, documented by VIGINUM, DFRLab,
  CheckFirst), sampled at random from their public news sitemaps.
- wire / mainstream (negative): BBC, DW, NPR, Guardian, Al Jazeera from their public RSS feeds, plus HARD negatives:
  Kyiv Independent, Ukrainska Pravda (English) and Meduza, which often cite Telegram channels of officials. These test
  the laundering and infrastructure signals for false alarms.

The demo URLs (backend/demo/cases.json) are excluded. Fixed seed. Run: python eval/build_articles.py
"""
from __future__ import annotations

import csv
import json
import random
import re
import urllib.request

from common import DATA, ROOT

SEED = 20261115
UA = {"User-Agent": "Mozilla/5.0 (research evaluation; Source Check, Junction 2026)"}
NETWORK = {"usa": 9, "ireland": 6, "canada": 5, "au": 5}
NEGATIVE = [  # (outlet, feed, n, path must match, hard)
    ("bbc", "https://feeds.bbci.co.uk/news/world/rss.xml", 4, r"bbc\.(co\.uk|com)/news/articles/", False),
    ("dw", "https://rss.dw.com/rdf/rss-en-world", 4, r"dw\.com/en/.+/a-\d+", False),
    ("npr", "https://feeds.npr.org/1004/rss.xml", 3, r"npr\.org/\d{4}/\d{2}/\d{2}/", False),
    ("guardian", "https://www.theguardian.com/world/rss", 3, r"theguardian\.com/world/\d{4}/", False),
    ("aljazeera", "https://www.aljazeera.com/xml/rss/all.xml", 3, r"aljazeera\.com/news/\d{4}/", False),
    ("kyivindependent", "https://kyivindependent.com/news-archive/rss/", 4, r"kyivindependent\.com/[a-z0-9-]+/?$", True),
    ("pravda.com.ua", "https://www.pravda.com.ua/eng/rss/", 2, r"pravda\.com\.ua/eng/news/", True),
    ("meduza", "https://meduza.io/rss/en/all", 2, r"meduza\.io/en/(news|feature)/", True),
]


def get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def links(xml: str) -> list[str]:
    found = re.findall(r"<(?:loc|link|guid[^>]*)>\s*(?:<!\[CDATA\[)?\s*(https?://[^<\]\s]+)", xml)
    found += re.findall(r'rdf:about="(https?://[^"]+)"', xml)
    return list(dict.fromkeys(u.split("#")[0].split("?")[0] for u in found))


def main() -> None:
    rnd = random.Random(SEED)
    demo = {c["url"].split("?")[0] for c in json.loads((ROOT / "backend/demo/cases.json").read_text())}
    rows = []
    for sub, n in NETWORK.items():
        urls = [u for u in links(get(f"https://{sub}.news-pravda.com/sitemap_news.xml")) if re.search(r"/\d+\.html$", u) and u not in demo]
        rows += [{"url": u, "group": "network", "outlet": f"{sub}.news-pravda.com", "hard": False} for u in rnd.sample(urls, min(n, len(urls)))]
    for outlet, feed, n, pat, hard in NEGATIVE:
        try:
            urls = [u for u in links(get(feed)) if re.search(pat, u) and "/live" not in u and u not in demo]
        except Exception as exc:     # a feed that is down just contributes nothing; reported below
            print(f"feed failed: {outlet}: {exc}")
            continue
        rows += [{"url": u, "group": "wire", "outlet": outlet, "hard": hard} for u in rnd.sample(urls, min(n, len(urls)))]
    net = [r for r in rows if r["group"] == "network"]
    neg = [r for r in rows if r["group"] == "wire"]
    rnd.shuffle(net)
    rnd.shuffle(neg)
    ordered = [r for pair in zip(neg, net) for r in pair] + neg[len(net):] + net[len(neg):]   # interleaved: a cut run stays balanced
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "articles.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "url", "group", "outlet", "hard"])
        w.writeheader()
        for i, r in enumerate(ordered):
            w.writerow({"id": f"a{i:02d}", **r})
    print(f"{len(net)} network + {len(neg)} wire -> {DATA / 'articles.csv'}")


if __name__ == "__main__":
    main()
