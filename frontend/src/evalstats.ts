// Formatting helpers for the methodology page. A metric is only shown when there is data behind it:
// a confusion matrix with no cases means "eval not run yet", never "0 %".
import type { Confusion } from './types';

export const total = (c: Confusion) => c.tp + c.fp + c.fn + c.tn;

export const hasRun = (c: Confusion) => total(c) > 0;

export function pct(x: number, c: Confusion): string {
  return hasRun(c) ? `${(x * 100).toFixed(1)} %` : 'no run yet';
}

/** 95 % Wilson score interval for k successes out of n, as [low, high] in 0..1; null when n is 0. */
export function wilson(k: number, n: number, z = 1.96): [number, number] | null {
  if (n <= 0) return null;
  const p = k / n, z2 = z * z, d = 1 + z2 / n;
  const c = (p + z2 / (2 * n)) / d, h = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / d;
  return [Math.max(0, c - h), Math.min(1, c + h)];
}

/** "95 % CI 12–34 %" for k of n; '' when there is no data. */
export function ciText(k: number, n: number): string {
  const w = wilson(k, n);
  return w ? `95 % CI ${Math.round(w[0] * 100)}–${Math.round(w[1] * 100)} %` : '';
}

// Names thresholds like `young_domain_days` for display.
export const label = (key: string) => key.replace(/_/g, ' ');
