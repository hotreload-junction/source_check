// Top-bar "Propaganda <-> Objective" view (PLAN F7). Context for the reader, never a verdict signal
// (DECISIONS: source quality is context). The bar position is the backend article verdict (bar.ts);
// the table restates backend facts per criterion. Cell strength (0..1) only sets the colour intensity.

import { markerPos } from './bar';
import type { TNode, Tree, View } from './tree';
import type { ClaimVerdict, Domain, RspEntry, Source, SourceType } from './types';

export interface Cell { items: string[]; strength: number } // strength 0 = no argument, 1 = strong argument
export type RowKey = 'sources' | 'fit' | 'variety' | 'wording' | 'other';
export interface Row { key: RowKey; label: string; basis: string; pro: Cell; con: Cell } // pro = objectivity, con = propaganda

const cell = (items: string[], s: number): Cell => ({ items, strength: items.length ? Math.max(0, Math.min(1, s)) : 0 });
const names = (l: string[]) => (l.length <= 2 ? l.join(' and ') : `${l.slice(0, 2).join(', ')} and ${l.length - 2} more`);
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

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

const TYPE_BASE: Record<SourceType, number> = { official: 0.9, academic: 0.9, reference: 0.75, news: 0.65, press_release: 0.4, blog: 0.4, social: 0.2, unknown: 0.5 };
export const TYPE_WORD: Record<SourceType, string> = { official: 'official', academic: 'academic', reference: 'reference', news: 'news', press_release: 'press release', blog: 'blog', social: 'social media', unknown: 'unknown' };
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

/** Bar position 0..1 (0 = propaganda); null for insufficient_evidence. */
export function barPos(v: View, t: Tree): number | null {
  if (!v.verdict) return null;
  const p = markerPos(v.verdict.label, t.claims.filter((c) => c.claim!.central !== false).map((c) => c.claim!.verdict));
  return p == null ? null : p / 100;
}

// Loaded-language word list for the wording row (English). Shown with every match, so it can be checked.
const LOADED: [string, RegExp][] = [
  ['regime', /\bregimes?\b/gi], ['puppet', /\bpuppets?\b/gi], ['junta', /\bjuntas?\b/gi], ['nazi', /\b(neo-?)?nazis?\b/gi],
  ['fascist', /\bfascis(t|ts|m)\b/gi], ['globalist', /\bglobalists?\b/gi], ['traitor', /\btraitors?\b/gi], ['so-called', /\bso-called\b/gi],
  ['false flag', /\bfalse[- ]flag\b/gi], ['staged', /\bstaged\b/gi], ['fabricated', /\bfabricated\b/gi], ['mainstream media', /\bmainstream media\b/gi],
  ['deep state', /\bdeep state\b/gi], ['russophobia', /\brussophob\w*/gi], ['warmonger', /\bwarmonger\w*/gi], ['collective west', /\bcollective west\b/gi],
  ['hysteria', /\bhysteri(a|cal)\b/gi], ['shocking', /\bshocking\b/gi], ['outrageous', /\boutrageous\b/gi], ['cover-up', /\bcover[- ]ups?\b/gi],
  ['propaganda', /\bpropaganda\b/gi], ['lies', /\blies\b/gi], ['wake up', /\bwake up\b/gi], ['sheeple', /\bsheeple\b/gi],
];

export function wordingCheck(text: string): { words: number; hits: [string, number][]; excl: number } {
  const prose = text.replace(/\]\([^)]*\)/g, ']'); // drop link targets, keep link text
  const words = prose.split(/\s+/).filter(Boolean).length;
  const hits = LOADED.map(([w, re]) => [w, (prose.match(re) ?? []).length] as [string, number]).filter(([, n]) => n > 0);
  const excl = (prose.match(/!(?!\[)/g) ?? []).length;
  return { words, hits, excl };
}

const BAD: Record<Exclude<ClaimVerdict, 'supported' | 'not_checkable'>, string> = {
  contradicted: 'contradicted by the cited source', distorted: 'distorted compared with the cited source', not_mentioned: 'not found in the cited source', unsourced: 'given without any source',
};

export function criteria(v: View, t: Tree): Row[] {
  const dom = new Map(v.domains.map((d) => [d.domain, d]));
  const artD = v.domains.find((d) => d.is_article_domain);
  const byDomain = new Map<string, Source>(); // origin sources = where a trace ends; one per domain
  t.nodes.filter((n) => n.kind === 'source' && !n.kids.length && n.source?.domain && n.source.kind !== 'self').forEach((n) => {
    if (!byDomain.has(n.source!.domain!)) byDomain.set(n.source!.domain!, n.source!);
  });
  const origins = [...byDomain.values()];
  // Variety is an argument FOR objectivity only where a source actually backs a claim: a source that contradicts, distorts or
  // does not contain what the article says it says is no evidence of breadth. Pages the SEARCH found (kind 'named',
  // found_source_ids; the article does not cite them) never count either. Every leaf is looked at, not only the first per domain.
  const found = new Set(v.claims.flatMap((c) => c.found_source_ids ?? []));
  const backs = (n: TNode) => n.source!.kind !== 'named' && !found.has(n.source!.id) && [...n.fcs].some((id) => t.by[id]?.claim?.verdict === 'supported');
  const backing = t.nodes.filter((n) => n.kind === 'source' && !n.kids.length && n.source?.domain && n.source.kind !== 'self' && backs(n)).map((n) => n.source!);
  const own = (s: Source) => {
    const d = dom.get(s.domain!);
    return s.domain === v.article.domain || !!d?.is_article_domain || (!!artD?.cluster_id && d?.cluster_id === artD.cluster_id);
  };
  const external = origins.filter((s) => !own(s));
  const externalBacking = backing.filter((s) => !own(s));
  const rspOf = (s: Source) => rspVerdict(dom.get(s.domain!)?.wikipedia_rsp ?? []);
  const doms = (l: Source[]) => names(l.map((s) => s.domain!));

  // 1. how good the sources are
  const rated = origins.map((s) => ({ s, r: rate(s, dom.get(s.domain!)) })).filter((x): x is { s: Source; r: number } => x.r != null);
  const n = rated.length || 1;
  const strong = origins.filter((s) => typeOf(s) === 'official' || typeOf(s) === 'academic');
  const weak = origins.filter((s) => ['press_release', 'blog', 'social'].includes(typeOf(s)));
  const net = origins.filter((s) => dom.get(s.domain!)?.known_network);
  const sPro: string[] = [];
  const sCon: string[] = [];
  if (strong.length) sPro.push(`${strong.length} of ${origins.length} origin sources are official or academic (${doms(strong)}).`);
  const rel = origins.filter((s) => rspOf(s) === 'reliable');
  if (rel.length) sPro.push(`Wikipedia's Perennial Sources list rates ${doms(rel)} as generally reliable.`);
  for (const [k, txt] of [['deprecated', 'deprecated'], ['unreliable', 'generally unreliable'], ['mixed', 'topic-dependent or without consensus']] as const) {
    const l = origins.filter((s) => rspOf(s) === k);
    if (l.length) sCon.push(`Wikipedia's Perennial Sources list rates ${doms(l)} as ${txt}.`);
  }
  if (net.length) sCon.push(`${doms(net)} is on a published influence-network list (${dom.get(net[0].domain!)!.known_network}).`);
  if (weak.length) sCon.push(`${weak.length} of ${origins.length} origin sources are press releases, blogs or social posts (${doms(weak)}): self-published, no independent check.`);
  const sources: Row = {
    key: 'sources', label: 'Quality of the sources',
    basis: origins.length ? `${rated.length} of ${plural(origins.length, 'origin source')} rated by source type and reputation lists${rated.length < origins.length ? `; ${origins.length - rated.length} unclassified, left out` : ''}` : 'No origin source found',
    pro: cell(sPro, rated.filter((x) => x.r >= 0.7).reduce((a, x) => a + (x.r - 0.5) * 2, 0) / n),
    con: cell(sCon, rated.filter((x) => x.r < 0.5).reduce((a, x) => a + (0.5 - x.r) * 2, 0) / n),
  };

  // 2. how well the claims match their sources (central claims; not_checkable is neutral)
  const central = t.claims.filter((c) => c.claim!.central !== false);
  const checkable = central.filter((c) => c.claim!.verdict !== 'not_checkable');
  const sup = checkable.filter((c) => c.claim!.verdict === 'supported');
  const fPro: string[] = [];
  const fCon: string[] = [];
  if (sup.length) {
    fPro.push(`${sup.length} of ${checkable.length} checkable claims are backed by a verbatim quote from their source.`);
  }
  (Object.keys(BAD) as (keyof typeof BAD)[]).forEach((k) => {
    const l = checkable.filter((c) => c.claim!.verdict === k);
    if (l.length) fCon.push(`${l.length === 1 ? 'Claim' : 'Claims'} ${l.map((c) => c.claimIdx).join(', ')} ${l.length === 1 ? 'is' : 'are'} ${BAD[k]}.`);
  });
  const fit: Row = {
    key: 'fit', label: 'Claims match their sources',
    basis: `${checkable.length} of ${plural(central.length, 'central claim')} checkable; not checkable is neutral`,
    pro: cell(fPro, checkable.length ? sup.length / checkable.length : 0),
    con: cell(fCon, checkable.length ? (checkable.length - sup.length) / checkable.length : 0),
  };

  // 3. variety: independent operators (shared analytics/AdSense ID = one operator) and source types.
  // Narrowness (con) looks at all external origin sources and is unchanged, so not_checkable stays neutral; breadth (pro) only counts
  // sources that back a claim.
  const operatorsOf = (l: Source[]) => new Set(l.map((s) => dom.get(s.domain!)?.cluster_id ?? s.domain!));
  const diversity = (l: Source[]) => {
    const ops = operatorsOf(l);
    const ty = new Set(l.map(typeOf).filter((x) => x !== 'unknown'));
    const opC = Math.min(1, Math.max(0, (ops.size - 1) / 3));
    const tyC = ty.size ? Math.min(1, (ty.size - 1) / 2) : null;
    return { ops, ty, div: l.length ? (tyC == null ? opC : (opC + tyC) / 2) : 0 };
  };
  const all = diversity(external);
  const bk = diversity(externalBacking);
  const operators = all.ops;
  const types = all.ty;
  const div = all.div;
  const ownN = origins.length - external.length;
  const vPro: string[] = [];
  const vCon: string[] = [];
  if (bk.ops.size >= 2) vPro.push(`${bk.ops.size} independent operators stand behind sources that back a claim.`);
  if (bk.ty.size >= 2) vPro.push(`The sources that back a claim are of ${bk.ty.size} different types (${[...bk.ty].map((x) => TYPE_WORD[x]).join(', ')}).`);
  if (ownN) vCon.push(`${plural(ownN, 'origin source')} belong${ownN === 1 ? 's' : ''} to the article's own operator.`);
  if (external.length > 1 && operators.size === 1) vCon.push('All other origin sources share one operator.');
  else if (external.length === 1) vCon.push('The claims rest on a single independent origin source.');
  else if (origins.length && !external.length) vCon.push('No independent origin source.');
  if (external.length > 1 && types.size === 1) vCon.push(`All sources are of one type (${TYPE_WORD[[...types][0]]}).`);
  const variety: Row = {
    key: 'variety', label: 'Variety of the sources',
    basis: origins.length ? `${plural(bk.ops.size, 'independent operator')}, ${plural(bk.ty.size, 'source type')} among the sources that back a claim (${plural(origins.length, 'origin source')} in total)` : 'No origin source found',
    pro: cell(vPro, bk.div),
    con: cell(vCon, Math.max(1 - div, origins.length ? ownN / origins.length : 0)),
  };

  // 4. wording: loaded-language word list on the article text
  const w = wordingCheck(v.article.text);
  const density = w.words ? ((w.hits.reduce((a, [, k]) => a + k, 0) + w.excl * 0.5) * 100) / w.words : 0;
  const wPro: string[] = [];
  const wCon: string[] = [];
  if (w.hits.length) wCon.push(`Loaded terms: ${w.hits.map(([t2, k]) => `“${t2}”${k > 1 ? ` ${k}×` : ''}`).join(', ')}.`);
  if (w.excl >= 3) wCon.push(`${w.excl} exclamation marks.`);
  if (w.words >= 80 && !w.hits.length && w.excl < 3) wPro.push(`No terms from the loaded-language list in ${w.words} words.`);
  const wording: Row = {
    key: 'wording', label: 'Wording',
    basis: w.words ? `Word-list check (${LOADED.length} English terms, exclamation marks), not an AI judgement` : 'Article text not available',
    pro: cell(wPro, 0.35), // absence of loaded words is weak evidence
    con: cell(wCon, density / 1.5),
  };

  // 5. everything else: infrastructure, citation circle, laundering
  const sg = v.signals;
  const oPro: string[] = [];
  const oCon: string[] = [];
  if (sg?.infrastructure.fired) oCon.push(`Infrastructure: ${sg.infrastructure.reasons.join(' · ')}.`);
  if (sg?.circle.fired && sg.circle.path) {
    const lab = (id: string) => (id === v.articleId ? 'the article' : t.by[id]?.kind === 'claim' ? `claim ${t.by[id].claimIdx}` : t.by[id]?.source?.domain ?? id);
    oCon.push(`Citation circle: ${sg.circle.path.map(lab).join(' → ')}.`);
  }
  if (sg?.laundering?.fired) oCon.push(`Laundering: ${sg.laundering.reasons.join(' · ')}.`);
  if (sg && !oCon.length) oPro.push('No shared-operator, young-domain, citation-circle or laundering signal.');
  const other: Row = {
    key: 'other', label: 'Other signals',
    basis: 'Infrastructure (analytics/AdSense IDs, domain age, network lists), citation circles, laundering',
    pro: cell(oPro, 0.4),
    con: cell(oCon, 0.5 + 0.25 * oCon.length),
  };

  return [sources, fit, variety, wording, other];
}
