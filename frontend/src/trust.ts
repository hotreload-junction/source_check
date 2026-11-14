// Source trust profile for the root panel: how established the origin sources are, how independent they are,
// plus a short rule-based summary. Context for the reader, never a verdict signal (DECISIONS: source quality is
// context). The weights below are a transparent heuristic; the UI shows a position and words, not a number.

import { markerPos, supportedShare } from './bar';
import type { Tree, View } from './tree';
import { srcName } from './theme';
import type { ClaimVerdict, Domain, RspEntry, Source, SourceType } from './types';

export interface Gauge { pos: number | null; word: string; basis: string } // pos 0..1, 1 = good; null = not enough data
export interface Trust { legitimacy: Gauge; diversity: Gauge; positives: string[]; negatives: string[]; summary: string }

const TYPE_BASE: Record<SourceType, number> = { official: 0.9, academic: 0.9, reference: 0.75, news: 0.65, press_release: 0.4, blog: 0.4, social: 0.2, unknown: 0.5 };
const TYPE_WORD: Record<SourceType, string> = { official: 'official', academic: 'academic', reference: 'reference', news: 'news', press_release: 'press release', blog: 'blog', social: 'social media', unknown: 'unknown' };

export type RspVerdict = 'deprecated' | 'unreliable' | 'reliable' | 'mixed';

/** Wikipedia Perennial Sources rows for one domain, folded into one verdict. null = not listed. */
export function rspVerdict(rows: RspEntry[]): RspVerdict | null {
  if (!rows.length) return null;
  const all = rows.flatMap((r) => r.statuses);
  if (rows.some((r) => r.blacklisted) || all.includes('deprecated')) return 'deprecated';
  if (all.length && all.every((s) => s === 'generally_unreliable')) return 'unreliable';
  if (all.length && all.every((s) => s === 'generally_reliable')) return 'reliable';
  return 'mixed';
}

const typeOf = (s: Source): SourceType => s.source_type ?? (s.kind === 'telegram' || s.kind === 'social' ? 'social' : 'unknown');

/** 0..1 standing of one origin source; null = nothing known about it (never guessed). */
function rate(s: Source, d: Domain | undefined): number | null {
  if (d?.known_network) return 0;
  const r = rspVerdict(d?.wikipedia_rsp ?? []);
  if (r === 'deprecated') return 0;
  if (r === 'unreliable') return 0.15;
  const t = typeOf(s);
  if (t === 'unknown' && !r) return null;
  const base = TYPE_BASE[t];
  if (r === 'reliable') return Math.max(base, 0.85);
  if (r === 'mixed') return Math.min(base, 0.45);
  return base;
}

const names = (l: string[]) => (l.length <= 2 ? l.join(' and ') : `${l.slice(0, 2).join(', ')} and ${l.length - 2} more`);
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

export function trustProfile(v: View, t: Tree): Trust {
  const dom = new Map(v.domains.map((d) => [d.domain, d]));
  const artD = v.domains.find((d) => d.is_article_domain);

  // origin sources = where a trace ends; one entry per domain
  const byDomain = new Map<string, Source>();
  t.nodes.filter((n) => n.kind === 'source' && !n.kids.length && n.source?.domain && n.source.kind !== 'self').forEach((n) => {
    if (!byDomain.has(n.source!.domain!)) byDomain.set(n.source!.domain!, n.source!);
  });
  const origins = [...byDomain.values()];
  const own = (s: Source) => {
    const d = dom.get(s.domain!);
    return s.domain === v.article.domain || !!d?.is_article_domain || (!!artD?.cluster_id && d?.cluster_id === artD.cluster_id);
  };
  const external = origins.filter((s) => !own(s));

  // legitimacy
  const rated = origins.map((s) => ({ s, r: rate(s, dom.get(s.domain!)) })).filter((x): x is { s: Source; r: number } => x.r != null);
  const lpos = rated.length ? rated.reduce((a, x) => a + x.r, 0) / rated.length : null;
  const legitimacy: Gauge = {
    pos: lpos,
    word: lpos == null ? 'Not enough data' : lpos >= 0.7 ? 'Established' : lpos >= 0.45 ? 'Mixed' : 'Doubtful',
    basis: origins.length
      ? `${rated.length} of ${plural(origins.length, 'origin source')} rated from source type and reputation lists${rated.length < origins.length ? ` · ${origins.length - rated.length} unclassified, left out` : ''}`
      : 'No origin source to rate',
  };

  // diversity: independent operators (shared analytics/AdSense ID = one operator) and source types
  const operators = new Set(external.map((s) => dom.get(s.domain!)?.cluster_id ?? s.domain!));
  const types = new Set(external.map(typeOf).filter((x) => x !== 'unknown'));
  const opC = Math.min(1, Math.max(0, (operators.size - 1) / 3));
  const tyC = types.size ? Math.min(1, (types.size - 1) / 2) : null;
  const dpos = !origins.length ? null : external.length ? (tyC == null ? opC : (opC + tyC) / 2) : 0;
  const diversity: Gauge = {
    pos: dpos,
    word: dpos == null ? 'Not enough data' : dpos >= 0.7 ? 'Diverse' : dpos >= 0.4 ? 'Some variety' : 'Narrow',
    basis: origins.length ? `${plural(operators.size, 'independent operator')} · ${plural(types.size, 'source type')} among ${plural(origins.length, 'origin source')}` : 'No origin source to compare',
  };

  // short summary + what speaks for / against
  const rsp = (k: RspVerdict) => origins.filter((s) => rspVerdict(dom.get(s.domain!)?.wikipedia_rsp ?? []) === k).map((s) => s.domain!);
  const net = origins.filter((s) => dom.get(s.domain!)?.known_network);
  const strong = origins.filter((s) => ['official', 'academic'].includes(typeOf(s))).map((s) => s.domain!);
  const weak = origins.filter((s) => ['press_release', 'blog', 'social'].includes(typeOf(s))).map((s) => s.domain!);
  const ownN = origins.length - external.length;

  const positives: string[] = [];
  const negatives: string[] = [];
  if (strong.length) positives.push(`${strong.length} of ${origins.length} origin sources are official or academic (${names(strong)}).`);
  if (rsp('reliable').length) positives.push(`Wikipedia's Perennial Sources list rates ${names(rsp('reliable'))} as generally reliable.`);
  if (operators.size >= 3) positives.push(`${operators.size} independent operators stand behind the sources.`);
  if (types.size >= 3) positives.push(`The sources are of ${types.size} different types (${[...types].map((x) => TYPE_WORD[x]).join(', ')}).`);
  if (rated.length && !rsp('deprecated').length && !rsp('unreliable').length && !net.length) positives.push('No origin source is rated unreliable or listed in an influence network.');

  if (rsp('deprecated').length) negatives.push(`${names(rsp('deprecated'))} is deprecated on Wikipedia's Perennial Sources list.`);
  if (rsp('unreliable').length) negatives.push(`${names(rsp('unreliable'))} is rated generally unreliable on Wikipedia's Perennial Sources list.`);
  if (rsp('mixed').length) negatives.push(`${names(rsp('mixed'))}: Wikipedia rates the reliability as topic-dependent or without consensus.`);
  if (net.length) negatives.push(`${names(net.map((s) => s.domain!))} is on a published influence-network list (${dom.get(net[0].domain!)!.known_network}).`);
  if (weak.length) negatives.push(`${weak.length} of ${origins.length} origin sources are press releases, blogs or social posts (${names(weak)}): self-published, no independent check.`);
  if (ownN) negatives.push(`${plural(ownN, 'origin source')} belong${ownN === 1 ? 's' : ''} to the article's own operator.`);
  else if (external.length > 1 && operators.size === 1) negatives.push('All origin sources belong to one operator.');
  else if (external.length === 1) negatives.push('The claims rest on a single origin source.');

  const standing = lpos == null ? 'Too few could be classified to rate their standing.' : lpos >= 0.7 ? 'Most are established or official.' : lpos >= 0.45 ? 'Their standing is mixed.' : 'Most are weak or unreliable.';
  const summary = origins.length
    ? `The claims trace back to ${plural(origins.length, 'origin source')} from ${plural(operators.size + (ownN ? 1 : 0), 'operator')}. ${standing}`
    : 'No origin source was found for the central claims, so source trust cannot be judged.';
  return { legitimacy, diversity, positives: positives.slice(0, 4), negatives: negatives.slice(0, 4), summary };
}

export interface Overview { pos: number | null; positives: string[]; negatives: string[] } // pos 0..1: 0 = propaganda, 1 = objective

const BAD: ClaimVerdict[] = ['distorted', 'contradicted', 'not_mentioned', 'unsourced'];

/**
 * Top-bar position + what pulls towards objectivity / propaganda. No score is invented: the position is the
 * backend article verdict's zone nudged by the supported share (bar.ts); the lists only restate backend facts
 * (verdicts, fired signals) plus the source trust lines. not_checkable never counts against the article.
 */
export function overview(v: View, t: Tree, tp: Trust): Overview {
  const central = t.claims.map((c) => c.claim!).filter((c) => c.central !== false);
  const verdicts = central.map((c) => c.verdict);
  const p = v.verdict ? markerPos(v.verdict.label, verdicts) : null;
  const sg = v.signals;
  const { supported, checkable } = supportedShare(verdicts);
  const bad = verdicts.filter((x) => BAD.includes(x)).length;
  const label = (id: string) => {
    const n = t.by[id];
    return id === v.articleId ? 'the article' : n?.kind === 'claim' ? `claim ${n.claimIdx}` : n?.source ? srcName(n.source) : id;
  };

  const positives: string[] = [];
  const negatives: string[] = [];
  if (supported) positives.push(`${supported} of ${checkable} checkable central claims are confirmed by their sources.`);
  if (sg && !sg.evidence.fired && !sg.infrastructure.fired && !sg.circle.fired && !sg.laundering?.fired) positives.push('None of the propaganda signals fired (evidence, infrastructure, citation circle, laundering).');
  positives.push(...tp.positives);

  if (sg?.evidence.fired) negatives.push(`Evidence signal: ${sg.evidence.detail}`);
  else if (bad) negatives.push(`${bad} of ${central.length} central claims are distorted, contradicted, not in their source or unsourced.`);
  if (sg?.infrastructure.fired) negatives.push(`Infrastructure signal: ${sg.infrastructure.reasons.join(' · ')}`);
  if (sg?.circle.fired && sg.circle.path) negatives.push(`Citation circle: ${sg.circle.path.map(label).join(' → ')}`);
  if (sg?.laundering?.fired) negatives.push(`Laundering signal: ${sg.laundering.reasons.join(' · ')}`);
  negatives.push(...tp.negatives);

  return { pos: p == null ? null : p / 100, positives, negatives };
}
