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
