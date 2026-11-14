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
| GET | `/api/demo` | the 3 demo cases: `[{"key","label","url","job_id","expected_verdict"}]` |
| GET | `/api/methodology` | `Methodology` (from `eval/results/metrics.json`) |

### POST /api/analyses
- `202` `{"job_id": "a1b2c3", "status": "queued", "cached": false}`
- `200` `{"job_id": "...", "status": "done", "cached": true}` when the normalised URL is cached and `force` is false
- `429` `{"error": "busy", "detail": "2 analyses running, try again in a minute"}` when `MAX_CONCURRENT_ANALYSES` is reached
- `422` invalid URL

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

Steps, in order: `scrape_article`, `extract_claims`, `fetch_sources`, `verify_claims`, `infra_signals`,
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
claims:   Claim[]            # max 6
sources:  Source[]
domains:  Domain[]
graph:    {nodes: GraphNode[], edges: GraphEdge[]}
signals:  Signals
verdict:  {label: ArticleVerdict, summary: str}   # summary = 1-2 plain-English sentences
audit:    {model_extract, model_verify, prompt_version, fuzzy_threshold, depth: 1|2, duration_s}
```

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
```
`ClaimVerdict` = `supported` | `distorted` | `contradicted` | `not_mentioned` | `unsourced` | `not_checkable`

### Source
```
id: "s1"
url: str
domain: str
kind: "link" | "named" | "telegram" | "social" | "self"
   # named = attribution without link, resolved via Exa (url may be null if not found)
attribution_text: str|null    # e.g. "according to the Finnish Ministry of Defence"
depth: 1 | 2
parent_source_id: str|null    # for depth 2
fetch_status: "ok" | "unreachable" | "paywall" | "blocked" | "not_fetched"
fetched_via: "context_dev" | "trafilatura" | "telegram" | null
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
```

### GraphNode / GraphEdge (Cytoscape-ready)
```
GraphNode: {id, type: "article"|"claim"|"source"|"domain", label, verdict: ClaimVerdict|null,
            cluster_id: str|null, depth: 0|1|2}
GraphEdge: {id, source, target,
            type: "makes_claim" | "cites" | "hosted_on" | "shares_infra" | "cites_back"}
```
`cites_back` marks an edge that closes a citation circle (depth 2).

### Signals
```
evidence:       {fired: bool, claim_ids: str[], detail: str}
infrastructure: {fired: bool, reasons: str[]}        # e.g. "a.com and b.net share G-ABC123"
circle:         {fired: bool, path: str[] | null}    # node ids, depth 2 only
```
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
