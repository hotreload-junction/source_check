import { describe, expect, it } from 'vitest';
import { buildTree, type View } from './tree';
import { overview, rspVerdict, trustProfile } from './trust';
import type { ArticleVerdict, Claim, ClaimVerdict, Domain, RspEntry, Signals, Source } from './types';

const claim = (id: string, source_ids: string[]): Claim => ({
  id, text: id, char_start: null, char_end: null, source_ids, verdict: 'supported', subtype: null, evidence: null, explanation: '',
});
const source = (id: string, extra: Partial<Source> = {}): Source => ({
  id, url: null, domain: id + '.example', kind: 'link', attribution_text: null, depth: 1, parent_source_id: null, fetch_status: 'ok', fetched_via: null, source_type: 'unknown', ...extra,
});
const domain = (name: string, extra: Partial<Domain> = {}): Domain => ({
  domain: name, created_at: null, age_days: null, analytics_ids: [], adsense_ids: [], cluster_id: null, is_article_domain: false, ...extra,
});
const rsp = (statuses: RspEntry['statuses'], blacklisted = false): RspEntry => ({ source: 'x', statuses, blacklisted, last_discussed: null, snapshot: '2026-11-14' });

function profile(sources: Source[], domains: Domain[] = []) {
  const claims = sources.filter((s) => s.depth === 1).map((s, i) => claim('c' + i, [s.id]));
  const v: View = {
    articleId: 'a', article: { title: 't', url: 'u', domain: 'article.example', text: '', published_at: null },
    claims, sources, domains, edges: [], signals: null, verdict: null, audit: null, focus: null, done: true,
  };
  return trustProfile(v, buildTree(v, new Map()));
}

describe('rspVerdict', () => {
  it('folds Perennial Sources rows into one verdict', () => {
    expect(rspVerdict([])).toBeNull();
    expect(rspVerdict([rsp(['generally_reliable'])])).toBe('reliable');
    expect(rspVerdict([rsp(['generally_unreliable'])])).toBe('unreliable');
    expect(rspVerdict([rsp(['generally_reliable']), rsp(['generally_unreliable'])])).toBe('mixed');
    expect(rspVerdict([rsp(['no_consensus'])])).toBe('mixed');
    expect(rspVerdict([rsp(['generally_unreliable'], true)])).toBe('deprecated');
    expect(rspVerdict([rsp(['deprecated'])])).toBe('deprecated');
  });
});

describe('trustProfile', () => {
  it('rates official and academic origins high and independent origins diverse', () => {
    const t = profile([
      source('a', { source_type: 'official' }), source('b', { source_type: 'academic' }),
      source('c', { source_type: 'news' }), source('d', { source_type: 'reference' }),
    ]);
    expect(t.legitimacy.pos).toBeGreaterThan(0.7);
    expect(t.legitimacy.word).toBe('Established');
    expect(t.diversity.word).toBe('Diverse');
    expect(t.positives.length).toBeGreaterThan(0);
    expect(t.negatives).toEqual([]);
  });

  it('puts a deprecated domain low and says why', () => {
    const t = profile([source('a', { source_type: 'news' })], [domain('a.example', { wikipedia_rsp: [rsp(['deprecated'])] })]);
    expect(t.legitimacy.pos).toBe(0);
    expect(t.legitimacy.word).toBe('Doubtful');
    expect(t.negatives[0]).toContain('a.example is deprecated');
  });

  it('flags an influence-network domain', () => {
    const t = profile([source('a', { source_type: 'news' })], [domain('a.example', { known_network: 'Portal Kombat' })]);
    expect(t.legitimacy.pos).toBe(0);
    expect(t.negatives.join(' ')).toContain('Portal Kombat');
  });

  it('counts domains sharing an analytics cluster as one operator', () => {
    const t = profile(
      [source('a', { source_type: 'news' }), source('b', { source_type: 'news' }), source('c', { source_type: 'news' })],
      [domain('a.example', { cluster_id: 'k1' }), domain('b.example', { cluster_id: 'k1' }), domain('c.example', { cluster_id: 'k1' })],
    );
    expect(t.diversity.word).toBe('Narrow');
    expect(t.diversity.basis).toContain('1 independent operator');
    expect(t.negatives).toContain('All origin sources belong to one operator.');
  });

  it('does not count sources of the article\'s own operator as independent', () => {
    const t = profile(
      [source('a', { source_type: 'news' }), source('b', { source_type: 'official' })],
      [domain('article.example', { is_article_domain: true, cluster_id: 'k1' }), domain('a.example', { cluster_id: 'k1' }), domain('b.example')],
    );
    expect(t.diversity.basis).toContain('1 independent operator');
    expect(t.negatives.join(' ')).toContain("article's own operator");
  });

  it('leaves unclassified sources out instead of guessing', () => {
    const t = profile([source('a'), source('b')]);
    expect(t.legitimacy.pos).toBeNull();
    expect(t.legitimacy.word).toBe('Not enough data');
    expect(t.legitimacy.basis).toContain('0 of 2');
    const mixed = profile([source('a', { source_type: 'official' }), source('b')]);
    expect(mixed.legitimacy.pos).toBe(0.9);
    expect(mixed.legitimacy.basis).toContain('1 unclassified');
  });

  it('gives no position when no claim has an origin source', () => {
    const t = profile([]);
    expect(t.legitimacy.pos).toBeNull();
    expect(t.diversity.pos).toBeNull();
    expect(t.summary).toContain('cannot be judged');
  });

  it('only rates where a trace ends (leaf sources), not intermediate ones', () => {
    const t = profile([
      source('a', { source_type: 'blog' }),
      source('b', { source_type: 'official', depth: 2, parent_source_id: 'a' }),
    ]);
    expect(t.legitimacy.pos).toBe(0.9);
  });
});

describe('overview', () => {
  const calm: Signals = { evidence: { fired: false, claim_ids: [], detail: '' }, infrastructure: { fired: false, reasons: [] }, circle: { fired: false, path: null } };
  function ov(label: ArticleVerdict, verdicts: ClaimVerdict[], signals: Signals) {
    const sources = verdicts.map((_, i) => source('s' + i, { source_type: 'official' }));
    const claims = verdicts.map((vd, i) => ({ ...claim('c' + i, ['s' + i]), verdict: vd }));
    const v: View = {
      articleId: 'a', article: { title: 't', url: 'u', domain: 'article.example', text: '', published_at: null },
      claims, sources, domains: [], edges: [], signals, verdict: { label, summary: '' }, audit: null, focus: null, done: true,
    };
    const t = buildTree(v, new Map());
    return overview(v, t, trustProfile(v, t));
  }

  it('puts a well sourced article on the objective side, with no propaganda reasons', () => {
    const o = ov('well_sourced', ['supported', 'supported', 'not_checkable'], calm);
    expect(o.pos).toBeGreaterThan(0.67);
    expect(o.positives[0]).toContain('2 of 2 checkable');
    expect(o.positives.join(' ')).toContain('None of the propaganda signals fired');
    expect(o.negatives).toEqual([]);
  });

  it('puts a propaganda pattern on the left and lists the fired signals', () => {
    const o = ov('propaganda_pattern', ['distorted', 'unsourced', 'supported'], {
      ...calm, evidence: { fired: true, claim_ids: ['c0', 'c1'], detail: '2 of 3 central claims are distorted or unsourced.' },
      infrastructure: { fired: true, reasons: ['a.example shares an analytics ID with b.example'] },
    });
    expect(o.pos).toBeLessThan(0.34);
    expect(o.negatives[0]).toContain('Evidence signal');
    expect(o.negatives.join(' ')).toContain('Infrastructure signal');
  });

  it('gives no position for insufficient evidence', () => {
    expect(ov('insufficient_evidence', ['not_checkable'], calm).pos).toBeNull();
  });
});
