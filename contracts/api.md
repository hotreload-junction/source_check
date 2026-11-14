# CiteTrace API contract v1

Owner: shared (backend implements, frontend consumes). Change rules: AGENTS.md section 4.
Examples: `contracts/examples/`. The frontend codes against those until the backend lands.
All JSON is UTF-8, snake_case, timestamps ISO 8601 UTC. Base path `/api`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{"ok": true, "version": "<git sha>"}` |
| POST | `/api/analyses` | start (or reuse cached) analysis. Body `{"url": "...", "force": false}` |
| GET | `/api/analyses/{job_id}` | job status + result when done (`Analysis`) |
| GET | `/api/analyses/{job_id}/events` | Server-Sent Events stream of progress (below) |
| GET | `/api/demo` | the 3 demo cases: `[{"key","label","url","job_id","expected_verdict","question"?}]` (`question` optional: display text for the landing example row) |
| GET | `/api/methodology` | `Methodology` (from `eval/results/metrics.json`) |

### POST /api/analyses
Body: `{"url": str, "force": bool = false, "question": str|null = null}`. `question` (F6, optional, max 500 chars) =
a reader's question or concern about ONE point of the article. It only decides which sentence(s) are checked in addition
(see `Result.focus`); it never changes the article verdict. The cache key is the normalised URL plus a hash of the
question (case-insensitive); demo buttons never send one.
- `202` `{"job_id": "a1b2c3", "status": "queued", "cached": false}`
- `200` `{"job_id": "...", "status": "done", "cached": true}` when the normalised URL is cached and `force` is false
- `429` `{"error": "busy", "detail": "2 analyses running, try again in a minute"}` when `MAX_CONCURRENT_ANALYSES` is reached
- `422` invalid URL (`invalid_url`) or question longer than 500 characters (`invalid_question`)

URL normalisation for the cache: lowercase host, strip `www.`, fragment, `utm_*`/`fbclid`/`gclid`, trailing slash.

### SSE events (`text/event-stream`)
Each event has `event:` = type and `data:` = JSON. Events are additive: the frontend can build the
graph from them, and the final `done` carries the full `Analysis` anyway.

| event | data |
|---|---|
| `progress` | `{"step": "scrape_article", "message": "Fetching article", "pct": 10}` |
| `node` | a `GraphNode` |
| `edge` | a `GraphEdge` |
| `claim` | a `Claim` (sent again when its verdict is final) |
| `done` | the full `Analysis` |
| `error` | `{"message": "..."}` |

Steps, in order: `scrape_article`, `extract_claims`, `focus` (only with a question), `fetch_sources`, `verify_claims`, `infra_signals`,
`depth2` (optional), `verdict`.
Fallback without SSE: poll `GET /api/analyses/{job_id}` every 2 s.

## Types

### Analysis
```
job_id: str
status: "queued" | "running" | "done" | "failed"
progress: {step: str, message: str, pct: int}
error: str | null
created_at, finished_at: str | null
result: Result | null        # set when status == "done"
```

### Result
```
article:  {url, domain, title, published_at|null, text_markdown}
claims:   Claim[]            # max 6 central (central: true) + max 2 focus-only claims (central: false) from the question
sources:  Source[]
domains:  Domain[]
graph:    {nodes: GraphNode[], edges: GraphEdge[]}
signals:  Signals
verdict:  {label: ArticleVerdict, summary: str}   # summary = 1-2 plain-English sentences; from CENTRAL claims only
focus:    Focus | null       # null when no question was sent
audit:    {model_extract, model_verify, prompt_version, fuzzy_threshold, depth: 1|2|3, duration_s,
           focus_prompt_version: str|null}   # null without a question
```
```
Focus: {question: str,                          # as sent, whitespace collapsed
        status: "found" | "not_in_article" | "failed",
        claim_ids: str[],                       # claims with focus: true (empty unless status == "found")
        note: str}                              # one plain-English sentence for the result header, built by code
```
Focus rules (DECISIONS F6): the question locates sentences (majority vote, max 2); they are verified with the same
prompt as every claim (the verify model never sees the question). A focus sentence that is also central keeps
`central: true`. A focus-only claim (`central: false`) is checked in its own pass and its sources (and what they cite)
stay out of the infrastructure / network / circle signals and get no domain node, so the article verdict and
`signals` are identical with or without a question. Example: `contracts/examples/analysis_focus.json`.

`article.text_markdown` is the full normalised article text, stored with the result (cached, so demo
cases render with all external APIs down). It is final: the backend never alters it after claims were
located, so `char_start`/`char_end` stay valid. Offsets count Unicode code points (Python `str` indices);
JavaScript strings count UTF-16 units, so clients must convert (e.g. `[...text]`) or fall back to searching
`claim.text` when `text_markdown.slice(start, end) !== claim.text`. The text is for display only and is not
served publicly outside the result. Fetched source texts are not part of the API.

### Claim
```
id: "c1"
text: str                     # as it appears in the article (verbatim span)
char_start, char_end: int|null  # [start, end) into article.text_markdown, in Unicode code points (see below)
source_ids: str[]             # empty -> verdict "unsourced"
found_source_ids: str[]       # additive: unsourced claim only; pages the search found (kind "named", url set). Never counts as a citation; verdict stays "unsourced"
verdict: ClaimVerdict
subtype: "self_citation" | "social_media" | null
evidence: {
  source_id: str,
  quote_original: str,        # verbatim from the source text
  quote_translation: str|null,  # English, null if source is English
  language: str,              # ISO 639-1
  fuzzy_score: float          # 0-100, rapidfuzz partial_ratio vs fetched source text
} | null
explanation: str              # one sentence: why this verdict
central: bool                 # additive (F6): true = one of the article's central claims, counts for the verdict
focus: bool                   # additive (F6): true = matches the reader's question (Result.focus); absent in older results = false
```
`ClaimVerdict` = `supported` | `distorted` | `contradicted` | `not_mentioned` | `unsourced` | `not_checkable`

### Source
```
id: "s1"
url: str|null        # null for a named source Exa could not resolve
domain: str|null     # null together with url; show attribution_text instead (clients must not assume a string)
kind: "link" | "named" | "telegram" | "social" | "self"
   # named = attribution without link, resolved via Exa (url may be null if not found)
attribution_text: str|null    # e.g. "according to the Finnish Ministry of Defence"
depth: 1 | 2 | 3
parent_source_id: str|null    # for depth >= 2
fetch_status: "ok" | "unreachable" | "paywall" | "blocked" | "not_fetched"
fetched_via: "context_dev" | "trafilatura" | "telegram" | null
source_type: "official" | "press_release" | "academic" | "reference" | "news" | "blog" | "social" | "unknown" | null
   # what KIND of document it is (PLAN Q1, backend/app/signals/source_type.py), fixed rule table on URL + raw HTML,
   # first match wins. Not a reliability rating: kremlin.ru is official, TASS is news. null = no URL (named stub).
   # Context for the reader only, never a verdict signal. Absent in results computed before Q1: treat as null.
source_type_reason: str|null  # the rule that fired, e.g. "press-release distribution service (prnewswire.com)"
```

### Domain
```
domain: str
created_at: str|null          # RDAP registration date
age_days: int|null
analytics_ids: str[]          # e.g. "G-XXXX", "UA-123-1", "GTM-XXXX"
adsense_ids: str[]            # "ca-pub-..."
cluster_id: str|null          # same id = shares at least one analytics/AdSense ID
is_article_domain: bool
known_network: str|null       # name of a documented influence network whose PUBLISHED host list contains this
                              # domain (or the article/source host on it), e.g. "Portal Kombat / Pravda network"
# Reputation badges (PLAN Q2): context for the reader, NEVER read by the verdict rules. Absent in older results = empty.
wikipedia_rsp: RspEntry[]     # rows of Wikipedia's Perennial Sources list naming this domain ([] = not listed);
                              # several rows are possible ("Fox News" / "Fox News (politics and science)")
tranco_rank: int|null         # rank in the Tranco top-sites snapshot (popularity, not reliability); null = not in top 100k
first_archived_at: str|null   # "YYYY-MM-DD" first Wayback Machine capture; null = never archived or lookup failed/off
reputation_notes: str[]       # plain-English lines with provenance, ready to show, e.g.
                              # "Wikipedia Perennial Sources rates \"RT\" deprecated, last discussed 2022 (snapshot 2026-11-14)"
                              # "Archived pages predate the current registration (2026-09-01) by 6 year(s) or more: ..."
```
```
RspEntry: {source: str, statuses: ("generally_reliable"|"no_consensus"|"generally_unreliable"|"deprecated")[],
           blacklisted: bool, last_discussed: int|null, snapshot: str}   # statuses: several = depends on topic
```

### GraphNode / GraphEdge (Cytoscape-ready)
```
GraphNode: {id, type: "article"|"claim"|"source"|"domain", label, verdict: ClaimVerdict|null,
            cluster_id: str|null, depth: 0|1|2|3}
GraphEdge: {id, source, target,
            type: "makes_claim" | "cites" | "hosted_on" | "shares_infra" | "cites_back"}
```
`cites_back` marks an edge that closes a citation circle (depth >= 2).
Multi-hop source tracing (default `MAX_DEPTH=3`; `MAX_DEPTH=1` switches it off; `audit.depth: 1|2|3`): a depth-N source
(N >= 2) is cited by a depth-(N-1) source in the passage relevant to the claim, so it has `parent_source_id` and a
`cites` edge **source -> source** (`depth: N` on the node). A deeper link back to the article's own domain is a
`kind: "self"`, `fetch_status: "not_fetched"` source. `cites_back` goes from the cited source (article domain or a
domain in the article's operator cluster) to the article node `a`; `signals.circle.path` =
`["a", "<claim>", "<depth-1 source>", ..., "<cited source>", "a"]`.

### Signals
```
evidence:       {fired: bool, claim_ids: str[], detail: str}
infrastructure: {fired: bool, reasons: str[]}        # e.g. "a.com and b.net share G-ABC123"
circle:         {fired: bool, path: str[] | null}    # node ids, depth >= 2 only
laundering:     {fired: bool, claim_ids: str[], reasons: str[]}   # republished Telegram/social channel posts
```
`infrastructure.reasons` also names hosts on a published network list (`backend/app/signals/known_networks.py`,
VIGINUM Portal Kombat list). `laundering` fires when the page declares a Telegram channel as its source ("Source:
Telegram ...") or when >= 2 claims, and more than half of the claims that cite a link, rest only on Telegram/social
posts; `claim_ids` = those claims. Cached results from before this field may lack `laundering` (treat as not fired).
`ArticleVerdict` = `well_sourced` | `weakly_sourced` | `propaganda_pattern` | `insufficient_evidence`
Rule: AGENTS.md section 7 / DECISIONS.md (Q17). Implemented only in `backend/app/verdict.py`.

### Methodology
```
generated_at: str
verify_model: str, prompt_version: str
pairs: {n, n_cross_language, precision, recall,       # for the positive class "not supported"
        confusion: {tp, fp, fn, tn}}
articles: {n_network, n_wire, fpr_wire, tpr_network,
           confusion: {tp, fp, fn, tn}}
thresholds: {name: value}     # e.g. {"min_bad_claims": 2, "young_domain_days": 90, "fuzzy": 90}
tuned_on: str, measured_on: str
error_cases: [{kind: "fp"|"fn", url_or_pair_id, expected, got, note}]
```


## Attributed claims without a link ("X said ...", "in a May interview with Fox Business, he ...")

The article gives no URL, so the backend searches for the statement (plan -> Exa search -> verify). The named person or
outlet is only the **attribution**, not a source: the pages the search found ARE the claim's sources (depth 1), and what those pages cite follows recursively as depth 2..`MAX_DEPTH`
(default `MAX_DEPTH=3`, same as for linked sources). Additive, no new fields:

```
claim  --cites-->  Source{kind:"named", depth:1, url:"https://www.foxbusiness.com/...", attribution_text:"Jensen Huang", fetch_status:"ok"}
                     \--cites-->  Source{depth:2, parent_source_id:<found page id>, ...}   # what that page cites (M1)
claim.source_ids = [found page id ...]                 # up to MAX_EVIDENCE_LINKS found pages
claim.evidence.source_id = the found page id           # the page that carries the verbatim quote (fuzzy_score checked)
edges: claim -> found page (`cites`), found page -> its own sources (`cites`, depth 2)
```
Nothing found: ONE stub Source `{kind:"named", depth:1, url:null, domain:null, attribution_text:"Jensen Huang"}` says
who the article attributes it to (no page, `not_checkable`). Several claims about the same person share the stub.

- Found and verified as the original (the venue's own page, transcript, official site): `verdict: "supported"`.
- Only an independent report of the quote (not the original): the page is the claim's source, but the verdict stays
  `not_checkable` with the explanation "Independent reporting confirms the quote, but the original ... could not be found."
  (`CORROBORATION_COUNTS=true` makes such a claim `supported`).
- Nothing found: only the stub; `verdict: "not_checkable"`, explanation starts with "Quote could not be verified".
- A found page that does not contain the quote is not linked and never counts against the article.
- Found pages (`kind:"named"` with a `url`) and everything below them are NOT cited by the article: they are excluded from
  the infrastructure, known-network and citation-circle signals (no domain node, no `hosted_on` edge). They are shown in
  the graph; their depth-2 sources are followed by M1 like any other.

## Self-citation chain ("source of the source") and sources found for unsourced claims

Additive, shapes as above.
- A link to the article's OWN site stays `kind:"self"`, depth 1, `not_checkable` / `self_citation` (never evidence). The
  backend now reads that page and follows the links in the passage the claim rests on (external only, no homepage, no
  second own-site hop, max `MAX_CHAIN_SOURCES`): `Source{kind:"link", depth:2, parent_source_id:<self row id>}`, edge
  `self row -> chain row` (`cites`). The chain source can CONFIRM the claim (`supported`, explanation "The article cites its own
  earlier piece (domain), which cites domain: ..."); it never counts against the article (same policy as a loosely attached link).
- Unsourced claim (`source_ids: []`): the backend still searches (Exa search + Exa Agent) and verifies what it finds like any
  source (quote first, fuzzy-checked). A page that supports the claim makes it `supported` with `evidence` pointing at that page
  (`evidence.source_id`); the explanation starts "The article gives no source for this claim. A page that supports it was found: ...".
  Such pages are listed in `Claim.found_source_ids` (rows `kind:"named"`, depth 1, `url` set), NOT in `source_ids`: the article cites
  none of them, so they feed no infrastructure signal and are not expanded further by M1; edges `claim -> found page` (`cites`).
  A claim that is still unsourced after its own search is also checked against the pages found for the OTHER unsourced claims (one
  page can support several claims; max 3 pages per claim, `CROSS_CHECK_FOUND=true`). Nothing supports it: the verdict stays
  `unsourced` (that describes how the ARTICLE sources the claim) and `evidence` is null. Pages that do not support the claim are
  dropped. `found_source_ids` is `[]` for every other claim.
