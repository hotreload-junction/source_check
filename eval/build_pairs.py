"""Build eval/data/pairs.jsonl from two public, expert-labelled datasets (no labels of our own).

- Check-COVID (Wang et al., ACL Findings 2023, MIT): claims journalists wrote in news articles, checked against the
  scientific paper the article cites (title + abstract). Only `is_auth` claims (written by journalists, not annotators).
  SUPPORT -> supported, REFUTE -> not supported (contradicted / distorted), NOTENOUGHINFO -> not supported (not mentioned).
- WiCE (Kamoi et al., EMNLP 2023, CC-BY-SA): Wikipedia sentences and the text of the web page they cite.
  supported -> supported, not_supported -> not supported. `partially_supported` is left out on purpose: our verify prompt
  treats a missing detail as not a distortion, so that label has no clean counterpart.

Stratified random sample with a fixed seed. Run: python eval/build_pairs.py
"""
from __future__ import annotations

import json
import random
import time
import urllib.request

from common import DATA, RUNS

CC = "https://raw.githubusercontent.com/posuer/check-covid/main/Check-COVID/"
WICE = "https://raw.githubusercontent.com/ryokamoi/wice/main/data/entailment_retrieval/claim/test.jsonl"
SEED = 20261115
PER_CLASS = {"cc": {"SUPPORT": 20, "REFUTE": 20, "NOTENOUGHINFO": 20}, "wice": {"supported": 20, "not_supported": 20}}


def get(url: str) -> str:
    """Downloads are kept in eval/runs/src/ (raw.githubusercontent.com rate-limits repeated fetches)."""
    local = RUNS / "src" / url.rsplit("/", 2)[-2] / url.rsplit("/", 1)[-1]
    if local.is_file():
        return local.read_text(encoding="utf-8")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                text = r.read().decode("utf-8")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(text, encoding="utf-8")
            return text
        except Exception:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


def main() -> None:
    rnd = random.Random(SEED)
    rows = []
    corpus = {d["cord_id"]: d for d in map(json.loads, get(CC + "corpus.json").splitlines()) if d}
    claims = [json.loads(x) for x in get(CC + "Check-COVID_all.json").splitlines() if x.strip()]
    for label, n in PER_CLASS["cc"].items():
        pool = [c for c in claims if c["is_auth"] and c["label"] == label and c["cord_id"] in corpus]
        for c in rnd.sample(pool, n):
            doc = corpus[c["cord_id"]]
            rows.append({"id": f"cc-{c['id']}", "dataset": "check-covid", "claim": c["claim"],
                         "source_text": doc["title"] + "\n\n" + " ".join(doc["abstract"]), "gold": label,
                         "not_supported": label != "SUPPORT", "lang": "en"})
    wice = [json.loads(x) for x in get(WICE).splitlines() if x.strip()]
    for label, n in PER_CLASS["wice"].items():
        for w in rnd.sample([w for w in wice if w["label"] == label], n):
            rows.append({"id": f"wice-{w['meta']['id']}", "dataset": "wice", "claim": w["claim"],
                         "source_text": "\n".join(w["evidence"]), "gold": label, "not_supported": label != "supported",
                         "lang": "en"})
    rnd.shuffle(rows)
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "pairs.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} pairs -> {DATA / 'pairs.jsonl'}")


if __name__ == "__main__":
    main()
