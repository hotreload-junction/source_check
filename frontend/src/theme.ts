import type { ArticleVerdict, ClaimVerdict, Source } from './types';

export const TH = {
  bg: '#f6f6f7', dot: '#d3d3d8', dr: 1.1, dr2: 1.3, ds: 24, ink: '#0b0b0f', mut: '#6c6c75', card: '#ffffff',
  line: '#e6e6ea', bar: '#f6f6f7', rad: 18, bw: 3.5,
  sh: '0 1px 2px #0000000f, 0 10px 28px #0000000b', font: "'Geist',sans-serif",
};

export const CL = { g: '#2e9d62', y: '#d9a514', o: '#e4772b', r: '#d23f4b', p: '#98a0a8' };
export const MONO = "'JetBrains Mono',monospace";

// Colours follow docs/graph-view.md: supported green, distorted amber,
// contradicted / not_mentioned / unsourced red, not_checkable grey (neutral).
export const CLAIM_V: Record<ClaimVerdict, { label: string; c: string; sym: string }> = {
  supported: { label: 'Supported', c: CL.g, sym: '✓' },
  distorted: { label: 'Distorted', c: CL.y, sym: '≈' },
  contradicted: { label: 'Contradicted', c: CL.r, sym: '✕' },
  not_mentioned: { label: 'Not mentioned', c: CL.r, sym: '?' },
  unsourced: { label: 'Unsourced', c: CL.r, sym: '∅' },
  not_checkable: { label: 'Not checkable', c: CL.p, sym: '–' },
};
export const CLAIM_ORDER: ClaimVerdict[] = ['supported', 'distorted', 'contradicted', 'not_mentioned', 'unsourced', 'not_checkable'];

export const ARTICLE_V: Record<ArticleVerdict, { label: string; c: string }> = {
  well_sourced: { label: 'Well sourced', c: CL.g },
  weakly_sourced: { label: 'Weakly sourced', c: CL.y },
  propaganda_pattern: { label: 'Propaganda pattern', c: CL.r },
  insufficient_evidence: { label: 'Insufficient evidence', c: CL.p },
};

export const SUBTYPE: Record<string, string> = { self_citation: 'Self-citation', social_media: 'Social media' };

export const KIND: Record<Source['kind'], string> = {
  link: 'Linked source', named: 'Named source', telegram: 'Telegram channel', social: 'Social media', self: 'Same-domain link',
};

export const FETCH: Record<Source['fetch_status'], string> = {
  ok: 'Fetched', unreachable: 'Unreachable', paywall: 'Paywalled', blocked: 'Blocked', not_fetched: 'Not fetched',
};

const PAL = ['#3b5bdb', '#7048e8', '#0b7285', '#a61e4d', '#5c6b7a', '#9c5a1a', '#2b8a3e', '#364fc7'];
export function pal(key: string): string {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 997;
  return PAL[h % PAL.length];
}

export const GLY = {
  article: 'M4 5h12v14H4zM16 9h4v8a2 2 0 0 1-2 2M7 9h6M7 13h6M7 16h4',
  claim: 'M6 17h3l2-4V7H5v6h3zM15 17h3l2-4V7h-6v6h3z',
  link: 'M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6',
  named: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  telegram: 'M21 4L3 11l6 2 2 6 3-4 5 4z',
  social: 'M4 5h16v11H9l-5 4z',
  self: 'M3 11l9-7 9 7M5 10v10h14V10',
  none: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM6 6l12 12',
};

export const fmtDate = (s: string | null) => (s ? s.slice(0, 10) : 'unknown');
