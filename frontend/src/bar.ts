// Position maths for the result bar. No score is invented: the marker sits inside the zone of the
// backend article verdict, nudged right by the share of supported claims among the checkable ones.
// not_checkable claims are neutral and stay off the scale.
import type { ArticleVerdict, ClaimVerdict } from './types';

const ZONE: Record<Exclude<ArticleVerdict, 'insufficient_evidence'>, [number, number]> = {
  propaganda_pattern: [0, 34],
  weakly_sourced: [34, 67],
  well_sourced: [67, 100],
};

export function supportedShare(verdicts: ClaimVerdict[]): { supported: number; checkable: number } {
  const checkable = verdicts.filter((v) => v !== 'not_checkable');
  return { supported: checkable.filter((v) => v === 'supported').length, checkable: checkable.length };
}

// Percent from the left, or null when the verdict is insufficient_evidence (no position on the scale).
export function markerPos(verdict: ArticleVerdict, verdicts: ClaimVerdict[]): number | null {
  if (verdict === 'insufficient_evidence') return null;
  const { supported, checkable } = supportedShare(verdicts);
  const share = checkable ? supported / checkable : 0.5;
  const [lo, hi] = ZONE[verdict];
  return lo + (hi - lo) * (0.1 + 0.8 * share);
}

const DOT_X: Record<ClaimVerdict, number | null> = {
  contradicted: 8, not_mentioned: 15, unsourced: 22, distorted: 48, supported: 88, not_checkable: null,
};

// Claim dots below the bar. Dots that land on the same spot are spread sideways by `off` px.
export function dotLayout(items: { id: string; verdict: ClaimVerdict }[]): { id: string; x: number; off: number }[] {
  const groups = new Map<number, string[]>();
  items.forEach((it) => {
    const x = DOT_X[it.verdict];
    if (x != null) groups.set(x, [...(groups.get(x) ?? []), it.id]);
  });
  const out: { id: string; x: number; off: number }[] = [];
  groups.forEach((ids, x) => ids.forEach((id, i) => out.push({ id, x, off: (i - (ids.length - 1) / 2) * 24 })));
  return out;
}
