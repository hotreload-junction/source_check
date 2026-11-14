import { describe, expect, it } from 'vitest';
import pravda from '../../contracts/examples/analysis_done.json';
import blog from './mocks/blog.json';
import wire from './mocks/wire.json';
import { buildTree, fromResult, type View } from './tree';
import { barPos, criteria, rspVerdict, wordingCheck, type RowKey } from './trust';
import type { Analysis, ArticleVerdict, Claim, ClaimVerdict, Domain, RspEntry, Signals, Source } from './types';

const claim = (id: string, source_ids: string[], verdict: ClaimVerdict = 'supported'): Claim => ({
  id, text: id, char_start: null, char_end: null, source_ids, verdict, subtype: null, evidence: null, explanation: '',
});
const source = (id: string, extra: Partial<Source> = {}): Source => ({
  id, url: null, domain: id + '.example', kind: 'link', attribution_text: null, depth: 1, parent_source_id: null, fetch_status: 'ok', fetched_via: null, source_type: 'unknown', ...extra,
});
const domain = (name: string, extra: Partial<Domain> = {}): Domain => ({
  domain: name, created_at: null, age_days: null, analytics_ids: [], adsense_ids: [], cluster_id: null, is_article_domain: false, ...extra,
});
const rsp = (statuses: RspEntry['statuses'], blacklisted = false): RspEntry => ({ source: 'x', statuses, blacklisted, last_discussed: null, snapshot: '2026-11-14' });
const calm: Signals = { evidence: { fired: false, claim_ids: [], detail: '' }, infrastructure: { fired: false, reasons: [] }, circle: { fired: false, path: null } };

function setup(sources: Source[], opt: { domains?: Domain[]; verdicts?: ClaimVerdict[]; text?: string; signals?: Signals; label?: ArticleVerdict } = {}) {
  const ones = sources.filter((s) => s.depth === 1);
  const claims = ones.map((s, i) => claim('c' + i, [s.id], opt.verdicts?.[i] ?? 'supported'));
  const v: View = {
    articleId: 'a', article: { title: 't', url: 'u', domain: 'article.example', text: opt.text ?? '', published_at: null },
    claims, sources, domains: opt.domains ?? [], edges: [], signals: opt.signals ?? calm,
    verdict: { label: opt.label ?? 'well_sourced', summary: '' }, audit: null, focus: null, done: true,
  };
  const t = buildTree(v, new Map());
  const rows = criteria(v, t);
  const row = (k: RowKey) => rows.find((r) => r.key === k)!;
  return { v, t, rows, row };
}

describe('rspVerdict', () => {
  it('folds Perennial Sources rows into one verdict', () => {
    expect(rspVerdict([])).toBeNull();
    expect(rspVerdict([rsp(['generally_reliable'])])).toBe('reliable');
    expect(rspVerdict([rsp(['generally_unreliable'])])).toBe('unreliable');
    expect(rspVerdict([rsp(['generally_reliable']), rsp(['generally_unreliable'])])).toBe('mixed');
    expect(rspVerdict([rsp(['no_consensus'])])).toBe('mixed');
    expect(rspVerdict([rsp(['generally_unreliable'], true)])).toBe('deprecated');
  });
});

describe('criteria', () => {
  it('has the five rows in order', () => {
    expect(setup([source('a')]).rows.map((r) => r.key)).toEqual(['sources', 'fit', 'variety', 'wording', 'other']);
  });

  it('source quality: official sources pull towards objectivity, deprecated ones towards propaganda', () => {
    const good = setup([source('a', { source_type: 'official' }), source('b', { source_type: 'academic' })]).row('sources');
    expect(good.pro.strength).toBeGreaterThan(0.7);
    expect(good.con.strength).toBe(0);
    const bad = setup([source('a', { source_type: 'news' })], { domains: [domain('a.example', { wikipedia_rsp: [rsp(['deprecated'])] })] }).row('sources');
    expect(bad.con.strength).toBe(1);
    expect(bad.con.items[0]).toContain('a.example as deprecated');
  });

  it('source quality: unclassified sources are left out, not guessed', () => {
    const r = setup([source('a'), source('b')]).row('sources');
    expect(r.pro.strength + r.con.strength).toBe(0);
    expect(r.basis).toContain('2 unclassified');
  });

  it('claim fit: names the claims per verdict, not_checkable stays neutral', () => {
    const r = setup([source('a'), source('b'), source('c'), source('d')], { verdicts: ['supported', 'distorted', 'not_checkable', 'unsourced'] }).row('fit');
    expect(r.pro.items[0]).toContain('1 of 3 checkable');
    expect(r.con.items).toEqual(['Claim 2 is distorted compared with the cited source.', 'Claim 4 is given without any source.']);
    expect(r.con.strength).toBeCloseTo(2 / 3);
  });

  it('variety: one operator behind several domains is narrow', () => {
    const ds = ['a', 'b', 'c'].map((x) => domain(x + '.example', { cluster_id: 'k1' }));
    const r = setup(['a', 'b', 'c'].map((x) => source(x, { source_type: 'news' })), { domains: ds }).row('variety');
    expect(r.con.items).toContain('All other origin sources share one operator.');
    expect(r.con.strength).toBe(1);
    expect(r.pro.items).toEqual([]);
  });

  it('variety: only sources under a SUPPORTED claim count as breadth (contradicted / distorted / not_mentioned add nothing)', () => {
    const src = ['a', 'b', 'c', 'd', 'e'].map((x, i) => source(x, { source_type: i % 2 ? 'official' : 'news' }));
    const bad = setup(src, { verdicts: ['contradicted', 'distorted', 'not_mentioned', 'contradicted', 'distorted'] }).row('variety');
    expect(bad.pro.items).toEqual([]);
    expect(bad.pro.strength).toBe(0);
    expect(bad.basis).toContain('0 independent operators');
    const mixed = setup(src, { verdicts: ['supported', 'contradicted', 'supported', 'contradicted', 'not_checkable'] }).row('variety');
    expect(mixed.pro.items[0]).toContain('2 independent operators');      // a and c only
    const good = setup(src, {}).row('variety');
    expect(good.pro.items[0]).toContain('5 independent operators');
  });

  it('variety: pages the search found (kind named / found_source_ids) are not breadth, even under a supported claim', () => {
    const a = source('a', { source_type: 'news' });
    const named = source('b', { source_type: 'official', kind: 'named' });
    const found = source('c', { source_type: 'academic' });
    const c1: Claim = { ...claim('c0', ['a']), }, c2: Claim = { ...claim('c1', ['b']) }, c3: Claim = { ...claim('c2', []), found_source_ids: ['c'] };
    const v: View = { articleId: 'a', article: { title: 't', url: 'u', domain: 'article.example', text: '', published_at: null },
      claims: [c1, c2, c3], sources: [a, named, found], domains: [], edges: [{ id: 'e', source: 'c2', target: 'c', type: 'cites' }], signals: calm,
      verdict: { label: 'well_sourced', summary: '' }, audit: null, focus: null, done: true };
    const r = criteria(v, buildTree(v, new Map())).find((x) => x.key === 'variety')!;
    expect(r.pro.items).toEqual([]);                  // only `a` is a cited, supporting source: one operator is no breadth
  });

  it('variety: a domain with a bad AND a good source counts through the good one', () => {
    const bad = source('x1', { domain: 'x.example', source_type: 'news' });
    const good = source('x2', { domain: 'x.example', source_type: 'news' });
    const other = source('y', { source_type: 'official' });
    const r = setup([bad, good, other], { verdicts: ['contradicted', 'supported', 'supported'] }).row('variety');
    expect(r.pro.items[0]).toContain('2 independent operators');
  });

  it('variety: sources of the article\'s own operator are not independent', () => {
    const r = setup([source('a', { source_type: 'news' }), source('b', { source_type: 'official' })], {
      domains: [domain('article.example', { is_article_domain: true, cluster_id: 'k1' }), domain('a.example', { cluster_id: 'k1' })],
    }).row('variety');
    expect(r.con.items[0]).toContain("article's own operator");
  });

  it('wording: lists loaded terms with counts; calm long text is a weak plus', () => {
    const loaded = setup([source('a')], { text: 'The regime staged it. The so-called experts lied, the regime says. [link](https://x.example/regime)' }).row('wording');
    expect(loaded.con.items[0]).toBe('Loaded terms: “regime” 2×, “so-called”, “staged”.');
    expect(loaded.con.strength).toBe(1);
    const calmText = setup([source('a')], { text: 'word '.repeat(120) }).row('wording');
    expect(calmText.pro.items[0]).toContain('120 words');
    expect(calmText.pro.strength).toBeLessThan(0.5);
    expect(setup([source('a')]).row('wording').basis).toBe('Article text not available');
  });

  it('other: fired infrastructure signal goes to propaganda, none fired is a plus', () => {
    const fired = setup([source('a')], { signals: { ...calm, infrastructure: { fired: true, reasons: ['a.example shares G-1 with article.example'] } } }).row('other');
    expect(fired.con.items[0]).toContain('shares G-1');
    expect(setup([source('a')]).row('other').pro.items).toHaveLength(1);
  });
});

describe('wordingCheck', () => {
  it('ignores link targets and markdown images', () => {
    expect(wordingCheck('see [here](https://propaganda.example) ![img](x.png)')).toMatchObject({ hits: [], excl: 0 });
  });
});

describe('barPos', () => {
  it('follows the article verdict zone; no position for insufficient evidence', () => {
    expect(barPos(setup([source('a')], { label: 'well_sourced' }).v, setup([source('a')]).t)!).toBeGreaterThan(0.67);
    const p = setup([source('a')], { label: 'insufficient_evidence' });
    expect(barPos(p.v, p.t)).toBeNull();
  });
});

describe('demo mocks', () => {
  const run = (a: unknown) => {
    const v = fromResult((a as Analysis).result!);
    const t = buildTree(v, new Map());
    return { pos: barPos(v, t), rows: criteria(v, t) };
  };

  it('land in different places so the demo shows the whole scale', () => {
    const [p, w] = [run(pravda), run(wire), run(blog)];
    expect(p.pos!).toBeLessThan(0.34);
    expect(w.pos!).toBeGreaterThan(0.67);
    const con = (x: { rows: { con: { strength: number } }[] }) => x.rows.reduce((a, r) => a + r.con.strength, 0);
    expect(con(p)).toBeGreaterThan(con(w));
  });
});
