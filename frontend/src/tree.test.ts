import { describe, expect, it } from 'vitest';
import done from '../../contracts/examples/analysis_done.json';
import wire from './mocks/wire.json';
import { buildTree, fromLive, fromResult, pairVerdict, type View } from './tree';
import type { Analysis, Claim, Source } from './types';

const result = (done as unknown as Analysis).result!;

const claim = (id: string, source_ids: string[], extra: Partial<Claim> = {}): Claim => ({
  id, text: id, char_start: null, char_end: null, source_ids, verdict: 'supported', subtype: null, evidence: null, explanation: '', ...extra,
});
const source = (id: string, extra: Partial<Source> = {}): Source => ({
  id, url: null, domain: id + '.example', kind: 'link', attribution_text: null, depth: 1, parent_source_id: null, fetch_status: 'ok', fetched_via: null, ...extra,
});
const view = (claims: Claim[], sources: Source[]): View => ({
  articleId: 'a', article: { title: 't', url: 'u', domain: 'd', text: '', published_at: null },
  claims, sources, domains: [], edges: [], signals: null, verdict: null, audit: null, focus: null, done: true,
});

describe('buildTree', () => {
  it('builds document -> claims -> sources from the contract example', () => {
    const t = buildTree(fromResult(result), new Map());
    expect(t.claims.map((c) => c.id)).toEqual(['c1', 'c2', 'c3', 'c4']);
    expect(t.by.s1.parent!.id).toBe('c1');
    expect(t.by['nosrc:c4'].kind).toBe('nosrc'); // unsourced claim gets a stub
    expect(t.N).toBe(1 + 4 + 3 + 1);
    expect(t.claims.every((c) => c.ord <= 4)).toBe(true); // claims reveal first
  });

  it('keeps a shared source once and links the second claim with an extra edge', () => {
    const t = buildTree(view([claim('c1', ['s1']), claim('c2', ['s1'])], [source('s1')]), new Map());
    expect(t.nodes.filter((n) => n.id === 's1')).toHaveLength(1);
    expect(t.edges.filter((e) => e.extra).map((e) => e.id)).toEqual(['c2->s1']);
    expect([...t.by.s1.fcs].sort()).toEqual(['c1', 'c2']);
    expect(t.by.s1.citedBy).toEqual(['c1', 'c2']);
  });

  it('places depth-2 sources under their parent source', () => {
    const t = buildTree(fromResult((wire as unknown as Analysis).result!), new Map());
    const deep = t.nodes.find((n) => n.source?.depth === 2)!;
    expect(deep.parent!.kind).toBe('source');
    expect(deep.d).toBe(3);
    expect(t.md).toBe(3);
  });

  it('places depth-3 sources recursively under depth-2 sources', () => {
    const s1 = source('s1', { depth: 1 });
    const s2 = source('s2', { depth: 2, parent_source_id: 's1' });
    const s3 = source('s3', { depth: 3, parent_source_id: 's2' });
    const t = buildTree(view([claim('c1', ['s1'])], [s1, s2, s3]), new Map());
    expect(t.by.s3.parent!.id).toBe('s2');
    expect(t.by.s3.d).toBe(4);
    expect(t.md).toBe(4);
  });

  it('keeps reveal order stable when new data arrives', () => {
    const order = new Map<string, number>();
    buildTree(view([claim('c1', ['s1'])], [source('s1')]), order);
    const t = buildTree(view([claim('c0', []), claim('c1', ['s1'])], [source('s1')]), order);
    expect(t.by.c1.ord).toBe(1);
    expect(t.by.s1.ord).toBe(2);
  });
});

describe('pairVerdict', () => {
  it('gives the verdict only to the evidence pair', () => {
    const c = claim('c1', ['s1', 's2'], { verdict: 'distorted', evidence: { source_id: 's2', quote_original: 'q', quote_translation: null, language: 'en', fuzzy_score: 95 } });
    expect(pairVerdict(c, 's2')).toEqual({ verdict: 'distorted', evidence: true });
    expect(pairVerdict(c, 's1')).toEqual({ verdict: null, evidence: false });
  });
});

describe('fromLive', () => {
  it('builds a partial view from streamed graph nodes and edges', () => {
    const v = fromLive({
      nodes: [
        { id: 'a', type: 'article', label: 'Title', verdict: null, cluster_id: null, depth: 0 },
        { id: 's1', type: 'source', label: 'x.example', verdict: null, cluster_id: null, depth: 1 },
      ],
      edges: [{ id: 'e', source: 'c1', target: 's1', type: 'cites' }],
      claims: [claim('c1', ['s1'])],
    }, 'https://news.example.com/x');
    expect(v.article.domain).toBe('news.example.com');
    expect(v.done).toBe(false);
    expect(buildTree(v, new Map()).by.s1.parent!.id).toBe('c1');
  });
});
