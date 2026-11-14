# DECISIONS

One line each, newest last. `time | handle | decision | why`. Don't relitigate; ask the lead instead.
Q-numbers refer to the project doc (claude.ai project "Junction 2026", `claude/doc.md`).

Fri | team | Q1 goal = Hostinger challenge prize; side quests Exa, Context.dev, MariaDB | demo must still land in peer review
Fri | team | Q2 users = fact-checkers/journalists; input = one article URL; 1-2 min runtime ok; browser extension only in pitch | focus
Fri | team | Q3 leo = pipeline (Python/FastAPI, scraping, LLM); louis = frontend, deploy, eval data | split
Fri | team | Q5 must: depth 1 + infra signals; MVP: depth 2 + citation circles; only if time: Exa findSimilar | scope
Fri | team | Q14 max 6 central claims per article, each marked sourced (link/named source) or unsourced | cost + clarity
Fri | team | Q18 sources = only links/attributions in the same sentence/paragraph as a central claim; same-domain links = `self_citation`, not evidence | avoids nav-link noise
Fri | team | Q19 Telegram via `t.me/s/<channel>` and checked normally; X/YouTube = `social_media`, not checkable, neutral | scrapeable vs not
Fri | team | Q20 articles in English, sources any language; LLM returns evidence in original + translation | Pravda sources are often Russian
Fri | team | Q6 claim verdicts: supported/distorted/contradicted/not_mentioned/unsourced/not_checkable (+ self_citation, social_media); article: well_sourced/weakly_sourced/propaganda_pattern/insufficient_evidence | German labels in doc map 1:1 (stützt/verdreht/widerspricht/erwähnt es nicht/unbelegt/nicht prüfbar; gut belegt/schwach belegt/Propaganda-Muster/zu wenig Evidenz)
Fri | team | Q15 quote first, verdict second: verbatim quote fuzzy-matched (rapidfuzz, ~90) against source text, else `supported` -> `not_checkable` | LLM can invent evidence
Fri | team | Q17 two-signal rule; start values tuned only on eval data, never on demo cases | honest FPR
Fri | team | Q17 evidence signal = >= 2 central claims distorted/contradicted/not_mentioned/unsourced | rule
Fri | team | Q17 infra signal = >= 1 source domain shares a GA/AdSense ID with the article or another source, OR >= 1 source domain younger than 90 days | rule
Fri | team | Q17 circle signal (depth 2 only) = a source leads back via citations to the article or its cluster | rule
Fri | team | Q17 verdict: > half of claims not_checkable -> insufficient_evidence; >= 2 different signals -> propaganda_pattern; only evidence signal -> weakly_sourced; else well_sourced | not_checkable never counts against; one error never yields propaganda_pattern
Fri | team | Q7 scraping Context.dev (fallback trafilatura); LLM Hostinger AI Router; unlinked named sources Exa; domain age RDAP; GA/AdSense IDs regex on HTML | stack
Fri | team | Q9 MariaDB main store with audit trail (evidence, model, prompt version); MariaDB Vector as memory across analyses once depth 1 runs | side quest + audit
Fri | team | Q16 background jobs: request returns job id, frontend polls or gets SSE; FastAPI background tasks + status in DB, no queue; max 2-3 concurrent, cache by URL | live demo without timeouts
Fri | team | Q21 open: cheap model (Haiku/mini class) for extraction, strong (Sonnet/Opus class) for verify, JSON, temperature 0; freeze verify model once eval runs | cost vs quality
Fri | team | Q11 UI: input (URL + 3 demo buttons), result (article left, Cytoscape graph right, operator clusters), public methodology page; cut in-text highlighting first, never methodology | Hostinger wants metrics
Fri | team | Q8 demo cases precomputed in cache: Pravda article, Reuters/AP article, sloppy harmless blog | covers TP, FP-proof, weak
Fri | team | Q10 eval: 40 claim-source pairs (precision/recall "not supported"); 10 network vs 10 wire articles (FPR on wire); eval cases != demo cases; report cross-language count | honest metrics
Sat 11:50 | leo | team = leo, louis + agents leo-be, leo-bg, louis-fe, louis-bg; no Hermes cloud teammate | setup
Sat 11:50 | leo | folders: backend/ (Python 3.12, FastAPI), frontend/, deploy/, eval/, contracts/ shared; API contract v1 in contracts/api.md | setup
Sat 13:45 | louis-fe | F1 stack: Vite + React 18 + TypeScript, vitest; `npm run build` -> static `frontend/dist/`; `npm run dev` (:5173, proxies /api to :8000) and `npm run dev:mock` (:5174, contracts/examples + fictional src/mocks, no backend) | louis asked for real + mock version on separate ports
Sat 13:45 | louis-fe | result view follows Louis' "Bedrock" design, named Source Trace: top-down canvas (document -> claims -> sources -> depth 2), own SVG/DOM renderer instead of Cytoscape; article text with claim highlights lives in the side panel, not a left column | louis chose the design; keeps pan/zoom, build-up animation, claim focus, edge dropdown
Sat 13:45 | louis-fe | no made-up scores (docs/graph-view.md): colours/labels = contract verdicts, only number shown is evidence.fuzzy_score; root ring = count of supported claims | honesty of detection logic
Sat 13:45 | louis-fe | "Classify this connection" = local reviewer override (6 contract verdicts, AI verdict tagged), browser-only, never changes the backend article verdict | keeps the design feature without a second verdict rule
Sat 14:00 | leo-be | Q21 (provisional) MODEL_VERIFY = gemini-4-argon, MODEL_EXTRACT = claude-haiku-5.5 (both via AI Router) | leo's pick after a 12-pair synthetic bench (not eval data): argon 11/12 verdicts, quotes all verbatim, cheaper than Sonnet but slow (~10 s/call) and needs max_tokens >= 4000 (reasoning). NOT frozen: re-compare vs claude-sonnet-5.5 / gpt-oss-120b on eval/ data before the eval run starts
Sat 14:00 | leo-be | Context.dev markdown keeps links only with includeLinks=true (163 vs 0 links on a test page); always set it (closes T4) | quote-first sourcing needs links
Sat | leo-be | Context.dev key allows 10 req/min (measured): shared RateLimiter, article waits up to 45s for a slot, sources fall back to trafilatura at once; CONTEXT_DEV_RPM env | live demo must not stall; demo cases are precomputed anyway
Sat | leo-bg | B6 interpretation of Q17: 0 claims -> insufficient_evidence; only the SOURCE domains' age counts for infra (not the article's); unknown age never young; any 2 of the 3 signals (incl. infra+circle without bad claims) -> propaganda_pattern; insufficient check comes first | rule text left these open; constants in backend/app/verdict.py
Sat | leo-be | Exa-resolved (named) sources: only an ORIGINAL (verify source_type=original: official statement, report, transcript, Q&A interview) can yield supported/distorted/contradicted; other outlets reporting the same = corroboration -> not_checkable (neutral). Candidates ranked first-hand-first, max 4 verify calls/claim, date window around publication. PROMPT_VERSION v2 | measured live: Exa finds originals for org reports (amnesty.org), rarely for ministries, never for spoken statements; 'official bias' query did not help
Sat | leo | Context.dev rate limit raised to 100 requests/min (leo asked them; leo-be verified with a 24-request burst, no 429). CONTEXT_DEV_RPM default 100; the limiter and the article-waits/source-falls-back logic stay as a safety net | fewer waits, more sources via Context.dev
