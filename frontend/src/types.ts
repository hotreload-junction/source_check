// Types from contracts/api.md (v1). Code against the contract, not against the backend implementation.

export type ClaimVerdict = 'supported' | 'distorted' | 'contradicted' | 'not_mentioned' | 'unsourced' | 'not_checkable';
export type ArticleVerdict = 'well_sourced' | 'weakly_sourced' | 'propaganda_pattern' | 'insufficient_evidence';

export interface Progress { step: string; message: string; pct: number }

export interface Evidence {
  source_id: string;
  quote_original: string;
  quote_translation: string | null;
  language: string;
  fuzzy_score: number;
}

export interface Claim {
  id: string;
  text: string;
  char_start: number | null;
  char_end: number | null;
  source_ids: string[];
  verdict: ClaimVerdict;
  subtype: 'self_citation' | 'social_media' | null;
  evidence: Evidence | null;
  explanation: string;
  central?: boolean; // F6: false = found only through the reader's question, does not count for the verdict
  focus?: boolean;   // F6: matches the reader's question (Result.focus)
}

// F6: optional reader question about one point of the article
export interface Focus {
  question: string;
  status: 'found' | 'not_in_article' | 'failed';
  claim_ids: string[];
  note: string;
}

export interface Source {
  id: string;
  url: string | null;
  domain: string | null;   // null for a named source (no link) that could not be resolved
  kind: 'link' | 'named' | 'telegram' | 'social' | 'self';
  attribution_text: string | null;
  depth: 1 | 2;
  parent_source_id: string | null;
  fetch_status: 'ok' | 'unreachable' | 'paywall' | 'blocked' | 'not_fetched';
  fetched_via: 'context_dev' | 'trafilatura' | 'telegram' | null;
}

export interface Domain {
  domain: string;
  created_at: string | null;
  age_days: number | null;
  analytics_ids: string[];
  adsense_ids: string[];
  cluster_id: string | null;
  is_article_domain: boolean;
  known_network?: string | null; // on a published influence-network list (e.g. VIGINUM Portal Kombat)
}

export interface GraphNode {
  id: string;
  type: 'article' | 'claim' | 'source' | 'domain';
  label: string;
  verdict: ClaimVerdict | null;
  cluster_id: string | null;
  depth: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: 'makes_claim' | 'cites' | 'hosted_on' | 'shares_infra' | 'cites_back';
}

export interface Signals {
  evidence: { fired: boolean; claim_ids: string[]; detail: string };
  infrastructure: { fired: boolean; reasons: string[] };
  circle: { fired: boolean; path: string[] | null };
  laundering?: { fired: boolean; claim_ids: string[]; reasons: string[] }; // optional: older cached results lack it
}

export interface Result {
  article: { url: string; domain: string; title: string; published_at: string | null; text_markdown: string };
  claims: Claim[];
  sources: Source[];
  domains: Domain[];
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  signals: Signals;
  verdict: { label: ArticleVerdict; summary: string };
  focus?: Focus | null; // optional: older cached results lack it
  audit: {
    model_extract: string;
    model_verify: string;
    prompt_version: string;
    fuzzy_threshold: number;
    depth: 1 | 2;
    duration_s: number;
    focus_prompt_version?: string | null;
  };
}

export interface Analysis {
  job_id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  progress: Progress;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
  result: Result | null;
}

export interface DemoCase { key: string; label: string; url: string; job_id: string; expected_verdict: ArticleVerdict }

export interface StartResponse { job_id: string; status: Analysis['status']; cached: boolean }

export interface Confusion { tp: number; fp: number; fn: number; tn: number }

export interface Methodology {
  generated_at: string;
  verify_model: string;
  prompt_version: string;
  pairs: { n: number; n_cross_language: number; precision: number; recall: number; confusion: Confusion };
  articles: { n_network: number; n_wire: number; fpr_wire: number; tpr_network: number; confusion: Confusion };
  thresholds: Record<string, number>;
  tuned_on: string;
  measured_on: string;
  error_cases: { kind: 'fp' | 'fn'; url_or_pair_id: string; expected: string; got: string; note: string }[];
}
