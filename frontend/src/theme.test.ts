import { describe, expect, it } from 'vitest';
import done from '../../contracts/examples/analysis_done.json';
import { pal, srcName } from './theme';
import { buildTree, fromResult } from './tree';
import type { Analysis } from './types';

// Regression: preview crashed with "can't access property length, e is null" when a named source (attribution
// without a link) could not be resolved: the backend sends domain: null and url: null for it.
describe('unresolved named source (domain null)', () => {
  const src = { domain: null, url: null, attribution_text: 'Welt am Sonntag' };

  it('srcName falls back to the attribution text, then the url, then a placeholder', () => {
    expect(srcName(src)).toBe('Welt am Sonntag');
    expect(srcName({ domain: 'a.com', url: 'https://a.com/x', attribution_text: 'x' })).toBe('a.com');
    expect(srcName({ domain: null, url: 'https://a.com/x', attribution_text: null })).toBe('https://a.com/x');
    expect(srcName({ domain: null, url: null, attribution_text: null })).toBe('Unnamed source');
  });

  it('pal() gives a colour for any display name', () => {
    expect(pal(srcName(src))).toMatch(/^#/);
    expect(pal(srcName({ domain: null, url: null, attribution_text: null }))).toMatch(/^#/);
  });

  it('buildTree keeps the source node when its domain is null', () => {
    const result = structuredClone((done as unknown as Analysis).result!);
    const s = result.sources[0];
    Object.assign(s, { kind: 'named', domain: null, url: null, attribution_text: 'Welt am Sonntag', fetch_status: 'not_fetched', fetched_via: null });
    const t = buildTree(fromResult(result), new Map());
    expect(t.nodes.some((n) => n.source?.id === s.id)).toBe(true);
  });
});
