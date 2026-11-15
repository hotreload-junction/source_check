import { describe, expect, it } from 'vitest';
import methodology from '../../contracts/examples/methodology.json';
import { ciText, hasRun, label, pct, total, wilson } from './evalstats';

const empty = { tp: 0, fp: 0, fn: 0, tn: 0 };

describe('eval stats helpers', () => {
  it('shows "no run yet" for an empty confusion matrix, never 0 %', () => {
    expect(hasRun(empty)).toBe(false);
    expect(pct(0, empty)).toBe('no run yet');
    expect(total(methodology.pairs.confusion)).toBe(0);
  });

  it('formats a real metric as a percentage', () => {
    expect(pct(0.9167, { tp: 11, fp: 1, fn: 1, tn: 7 })).toBe('91.7 %');
  });

  it('makes threshold keys readable', () => {
    expect(label('young_domain_days')).toBe('young domain days');
  });
});


describe('wilson', () => {
  it('gives a wide upper bound for 0 of 10 and none for n = 0', () => {
    const w = wilson(0, 10)!;
    expect(w[0]).toBe(0);
    expect(w[1]).toBeCloseTo(0.278, 2);
    expect(wilson(0, 0)).toBeNull();
    expect(ciText(0, 0)).toBe('');
  });
});
