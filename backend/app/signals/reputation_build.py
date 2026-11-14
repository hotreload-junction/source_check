"""Rebuild the dated reputation snapshots in `signals/data/` (PLAN Q2). Manual run only, never in tools/check:

    cd backend && python -m app.signals.reputation_build [--rsp-dir DIR]

- Wikipedia "Reliable sources/Perennial sources" (WP:RSP, CC BY-SA 4.0): every table row's status and the domains in
  its "Uses" column, from the 8 table subpages + the overflow page /X, with the retrieval date.
- Tranco top sites (https://tranco-list.eu, pay-level domains): the first `TRANCO_KEEP` ranks of the latest daily list.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .reputation import DATA_DIR, RSP_FILE, RSP_STATUS, TRANCO_FILE, TRANCO_KEEP, TRANCO_META_FILE

UA = {"User-Agent": "CiteTrace-research/0.1"}
RSP_PAGES = [f"Wikipedia:Reliable sources/Perennial sources/{p}" for p in ("1", "2", "3", "4", "5", "6", "7", "8", "X")]

_ROW = re.compile(r'^\|-\s*class="s-[^"]*"\s*id="([^"]+)"', re.M)
_STATUS = re.compile(r"\{\{\s*WP:RSPSTATUS\s*\|\s*([a-z]+)\s*(\|[^}]*)?\}\}")
_USES = re.compile(r"\{\{\s*WP:RSPUSES\s*\|([^}]*)\}\}")
_LAST = re.compile(r"\{\{\s*WP:RSPLAST\s*\|\s*(\d{4})")


def parse_rsp(wikitext: str) -> list[dict]:
    """One entry per table row: name, statuses (in order, deduplicated), blacklisted, last discussed year, domains."""
    entries = []
    starts = [(m.start(), m.group(1)) for m in _ROW.finditer(wikitext)]
    for i, (pos, name) in enumerate(starts):
        row = wikitext[pos: starts[i + 1][0] if i + 1 < len(starts) else len(wikitext)]
        statuses, blacklisted = [], False
        for code, extra in _STATUS.findall(row):
            st = RSP_STATUS.get(code)
            if st and st not in statuses:
                statuses.append(st)
            blacklisted |= bool(extra and re.search(r"\bb\s*=\s*y", extra))
        domains = []
        for uses in _USES.findall(row):
            for d in uses.split("|"):
                d = d.strip().lower().removeprefix("www.")
                if d and "=" not in d and "/" not in d and "." in d and d not in domains:   # path entries = part of a site only
                    domains.append(d)
        if not statuses or not domains:
            continue
        last = _LAST.search(row)
        entries.append({"source": name.strip(), "statuses": statuses, "blacklisted": blacklisted,
                        "last_discussed": int(last.group(1)) if last else None, "domains": domains})
    return entries


def build_rsp(client: httpx.Client, rsp_dir: Path | None = None) -> dict:
    """`rsp_dir`: read the wikitext from files p1.wiki ... p8.wiki, pX.wiki instead (Wikimedia may refuse scripted
    clients: download https://en.wikipedia.org/w/index.php?title=Wikipedia:Reliable_sources/Perennial_sources/<n>&action=raw)."""
    entries, pages = [], []
    for title in RSP_PAGES:
        if rsp_dir is not None:
            text = (rsp_dir / f"p{title.rsplit('/', 1)[1]}.wiki").read_text(encoding="utf-8")
        else:
            r = client.get("https://en.wikipedia.org/w/index.php", params={"title": title, "action": "raw"})
            r.raise_for_status()
            text = r.text
        rows = parse_rsp(text)
        if not rows and not title.endswith("/X"):
            raise RuntimeError(f"no rows parsed from {title}: table format changed?")
        entries += rows
        pages.append(title)
    return {"list": "Wikipedia: Reliable sources/Perennial sources", "license": "CC BY-SA 4.0",
            "url": "https://en.wikipedia.org/wiki/Wikipedia:Reliable_sources/Perennial_sources",
            "retrieved": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "pages": pages, "entries": entries}


def build_tranco(client: httpx.Client) -> tuple[list[str], dict]:
    list_id = client.get("https://tranco-list.eu/top-1m-id").text.strip()
    meta = client.get(f"https://tranco-list.eu/api/lists/id/{list_id}").json()
    z = client.get("https://tranco-list.eu/top-1m.csv.zip")
    z.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        rows = csv.reader(io.TextIOWrapper(zf.open(zf.namelist()[0]), "utf-8"))
        domains = [d for _, d in (next(rows) for _ in range(TRANCO_KEEP))]
    return domains, {"list": "Tranco", "list_id": list_id, "created_on": meta.get("created_on", "")[:10],
                     "url": f"https://tranco-list.eu/list/{list_id}/1000000", "kept_ranks": TRANCO_KEEP,
                     "retrieved": datetime.now(timezone.utc).strftime("%Y-%m-%d")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rsp-dir", type=Path, help="directory with p1.wiki ... p8.wiki, pX.wiki (raw WP:RSP wikitext)")
    args = ap.parse_args(argv)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True, headers=UA) as client:
        rsp = build_rsp(client, args.rsp_dir)
        RSP_FILE.write_text(json.dumps(rsp, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"RSP: {len(rsp['entries'])} entries, {sum(len(e['domains']) for e in rsp['entries'])} domains")
        domains, meta = build_tranco(client)
        with gzip.GzipFile(TRANCO_FILE, "wb", mtime=0) as f:     # mtime=0: rebuilding the same list = same bytes
            f.write(("\n".join(domains) + "\n").encode())
        TRANCO_META_FILE.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
        print(f"Tranco {meta['list_id']} ({meta['created_on']}): top {len(domains)} kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
