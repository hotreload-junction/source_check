// Formatting helpers for the methodology page. A metric is only shown when there is data behind it:
// a confusion matrix with no cases means "eval not run yet", never "0 %".
import type { Confusion } from './types';

export const total = (c: Confusion) => c.tp + c.fp + c.fn + c.tn;

export const hasRun = (c: Confusion) => total(c) > 0;

export function pct(x: number, c: Confusion): string {
  return hasRun(c) ? `${(x * 100).toFixed(1)} %` : 'no run yet';
}

// Names thresholds like `young_domain_days` for display.
export const label = (key: string) => key.replace(/_/g, ' ');
