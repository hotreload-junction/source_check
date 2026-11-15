// Builds the top-down trace tree (document -> claims -> sources -> sources of sources) from contract data.
// Each source appears once (docs/graph-view.md): its first citing claim owns it in the tree, other
// claims reach it through extra edges. Claims without sources get a "no source" stub.

import type { Claim, ClaimSourceCheck, ClaimVerdict, Domain, Focus, GraphEdge, GraphNode, Overview, Result, Signals, Source } from './types';

export interface View {
  articleId: string;
  article: { title: string; url: string; domain: string; text: string; published_at: string | null };
  claims: Claim[];
  sources: Source[];
  domains: Domain[];
  edges: GraphEdge[];
  signals: Signals | null;
  verdict: Result['verdict'] | null;
  overview?: Overview | null; // display only; absent while streaming and in older results
  audit: Result['audit'] | null;
  focus: Focus | null;
  done: boolean;
}

export type Kind = 'root' | 'claim' | 'source' | 'nosrc';

export interface TNode {
  id: string;
  kind: Kind;
  parent: TNode | null;
  kids: TNode[];
  ord: number; // reveal order (0 = document)
  d: number; // row
  fc: string | null; // owning claim
  fcs: Set<string>; // every claim whose trace reaches this node
  s: number; // horizontal slot 0..1
  desc: TNode[];
  claim?: Claim;
  claimIdx?: number;
  source?: Source;
  citedBy: string[]; // claim ids citing this source directly
}

export interface TEdge {
  id: string;
  from: TNode;
  to: TNode;
  type: 'claim' | 'cites' | 'sub' | 'nosrc' | 'back';
  extra: boolean;
  claim: Claim | null; // set for claim -> source pairs and stubs
  verdict: ClaimVerdict | null; // backend verdict for this claim-source pair, null = cited but not judged
  evidence: boolean; // this pair carries the claim's evidence quote
}

export interface Tree { root: TNode; nodes: TNode[]; edges: TEdge[]; claims: TNode[]; by: Record<string, TNode>; L: number; md: number; N: number }

export function fromResult(r: Result): View {
  const art = r.graph.nodes.find((n) => n.type === 'article');
  return {
    articleId: art ? art.id : 'a',
    article: { title: r.article.title, url: r.article.url, domain: r.article.domain, text: r.article.text_markdown, published_at: r.article.published_at },
    claims: r.claims, sources: r.sources, domains: r.domains, edges: r.graph.edges,
    signals: r.signals, verdict: r.verdict, overview: r.overview ?? null, audit: r.audit, focus: r.focus ?? null, done: true,
  };
}

export interface Live { nodes: GraphNode[]; edges: GraphEdge[]; claims: Claim[] }
export const emptyLive = (): Live => ({ nodes: [], edges: [], claims: [] });

// Partial view while SSE events stream in. Sources are only known as graph nodes until `done`.
export function fromLive(l: Live, url: string): View {
  const art = l.nodes.find((n) => n.type === 'article');
  const cites = l.edges.filter((e) => e.type === 'cites');
  const claims: Claim[] = [...l.claims];
  l.nodes.filter((n) => n.type === 'claim' && !claims.some((c) => c.id === n.id)).forEach((n) =>
    claims.push({ id: n.id, text: n.label, char_start: null, char_end: null, source_ids: cites.filter((e) => e.source === n.id).map((e) => e.target), verdict: n.verdict || 'not_checkable', subtype: null, evidence: null, explanation: '' }),
  );
  const sources: Source[] = l.nodes.filter((n) => n.type === 'source').map((n) => ({
    id: n.id, url: null, domain: n.label, kind: 'link', attribution_text: null,
    depth: (n.depth >= 3 ? 3 : n.depth === 2 ? 2 : 1) as 1 | 2 | 3,
    parent_source_id: null, fetch_status: 'not_fetched', fetched_via: null,
  }));
  let domain = '';
  try { domain = new URL(url).hostname; } catch { /* not a URL yet */ }
  return {
    articleId: art ? art.id : 'a',
    article: { title: art ? art.label : url, url, domain, text: '', published_at: null },
    claims, sources, domains: [], edges: l.edges, signals: null, verdict: null, audit: null, focus: null, done: false,
  };
}

/** The backend's check of this claim against this one source (every read page has one; older results have none). */
export function pairCheck(c: Claim, sid: string): ClaimSourceCheck | null {
  return c.source_checks?.find((k) => k.source_id === sid) ?? null;
}

export function pairVerdict(c: Claim, sid: string): { verdict: ClaimVerdict | null; evidence: boolean } {
  if (c.evidence && c.evidence.source_id === sid) return { verdict: c.verdict, evidence: true };
  if (!c.evidence && c.source_ids.length === 1 && c.source_ids[0] === sid) return { verdict: c.verdict, evidence: false };
  const k = pairCheck(c, sid); // a page the claim verdict does not rest on: its own verdict
  return { verdict: k ? k.verdict : null, evidence: false };
}

// `order` keeps reveal positions stable while data streams in; pass a fresh Map for a new job.
export function buildTree(v: View, order: Map<string, number>): Tree {
  const by: Record<string, TNode> = {};
  const mk = (id: string, kind: Kind, extra: Partial<TNode> = {}): TNode =>
    (by[id] = { id, kind, parent: null, kids: [], ord: 0, d: 0, fc: null, fcs: new Set(), s: 0, desc: [], citedBy: [], ...extra });
  const root = mk(v.articleId, 'root');
  const srcById = new Map(v.sources.map((s) => [s.id, s]));
  const claimIds = new Set(v.claims.map((c) => c.id));

  const adj = new Map<string, string[]>();
  const add = (a: string, b: string) => {
    if (a === b) return;
    const l = adj.get(a) ?? [];
    if (!l.includes(b)) l.push(b);
    adj.set(a, l);
  };
  const back: [string, string][] = [];
  v.claims.forEach((c) => c.source_ids.forEach((s) => srcById.has(s) && add(c.id, s)));
  v.sources.forEach((s) => s.parent_source_id && srcById.has(s.parent_source_id) && add(s.parent_source_id, s.id));
  v.edges.forEach((e) => {
    if (e.type !== 'cites' && e.type !== 'cites_back') return;
    if (!claimIds.has(e.source) && !srcById.has(e.source)) return;
    if (e.type === 'cites' && srcById.has(e.target)) add(e.source, e.target);
    else if (e.type === 'cites_back') back.push([e.source, e.target]);
  });

  const claims = v.claims.map((c, i) => {
    const n = mk(c.id, 'claim', { claim: c, claimIdx: i + 1, parent: root, d: 1, fc: c.id });
    root.kids.push(n);
    return n;
  });

  const placed = new Set<string>();
  const extra: [string, string][] = [];
  const visit = (pid: string, pn: TNode, d: number, fc: string) => {
    for (const sid of adj.get(pid) ?? []) {
      if (placed.has(sid)) { extra.push([pid, sid]); continue; }
      placed.add(sid);
      const n = mk(sid, 'source', { source: srcById.get(sid), parent: pn, d, fc });
      pn.kids.push(n);
      visit(sid, n, d + 1, fc);
    }
  };
  claims.forEach((c) => {
    visit(c.id, c, 2, c.id);
    // while streaming, sources are not known yet: only a finished analysis may say "no source"
    if (v.done && !c.kids.length && !(adj.get(c.id) ?? []).length && c.claim!.source_ids.length === 0) {
      c.kids.push(mk('nosrc:' + c.id, 'nosrc', { parent: c, d: 2, fc: c.id, claim: c.claim }));
    }
  });

  // which claims reach which node (tree + extra edges)
  claims.forEach((c) => {
    const seen = new Set<string>();
    const walk = (id: string) => {
      if (seen.has(id)) return;
      seen.add(id);
      by[id].fcs.add(c.id);
      by[id].kids.forEach((k) => walk(k.id));
      (adj.get(id) ?? []).forEach((s) => by[s] && walk(s));
    };
    walk(c.id);
  });
  v.claims.forEach((c) => (adj.get(c.id) ?? []).forEach((s) => by[s] && by[s].citedBy.push(c.id)));

  // reveal order: claims first, then each claim's subtree (as in the design), stable across rebuilds
  const list: TNode[] = [...claims];
  const sub = (n: TNode) => n.kids.forEach((k) => { list.push(k); sub(k); });
  claims.forEach(sub);
  let next = order.size ? Math.max(...order.values()) : 0;
  list.forEach((n) => { if (!order.has(n.id)) order.set(n.id, ++next); });
  [...list].sort((a, b) => order.get(a.id)! - order.get(b.id)!).forEach((n, i) => (n.ord = i + 1));
  claims.forEach((c) => {
    const ds: TNode[] = [];
    const g = (n: TNode) => n.kids.forEach((k) => { ds.push(k); g(k); });
    g(c);
    c.desc = ds;
  });

  // horizontal slots: leaves evenly spaced, parents centred over their kids
  const nodes = [root, ...list];
  const leaves = nodes.filter((n) => !n.kids.length).length || 1;
  let li = 0;
  const slot = (n: TNode) => {
    if (!n.kids.length) n.s = (li++ + 0.5) / leaves;
    else { n.kids.forEach(slot); n.s = n.kids.reduce((a, k) => a + k.s, 0) / n.kids.length; }
  };
  slot(root);
  const md = Math.max(1, ...nodes.map((n) => n.d));

  const edges: TEdge[] = [];
  const pair = (from: TNode, to: TNode, isExtra: boolean): TEdge => {
    const c = from.kind === 'claim' ? from.claim! : null;
    const pv = c && to.kind === 'source' ? pairVerdict(c, to.id) : { verdict: null, evidence: false };
    const type: TEdge['type'] = from.kind === 'root' ? 'claim' : to.kind === 'nosrc' ? 'nosrc' : c ? 'cites' : 'sub';
    return { id: from.id + '->' + to.id, from, to, type, extra: isExtra, claim: c ?? to.claim ?? null, verdict: type === 'nosrc' ? 'unsourced' : type === 'sub' ? (to.source?.check?.verdict ?? null) : pv.verdict, evidence: pv.evidence };
  };
  list.forEach((n) => edges.push(pair(n.parent!, n, false)));
  extra.forEach(([a, b]) => edges.push(pair(by[a], by[b], true)));
  back.forEach(([a, b]) => {
    const to = by[b] ?? root;
    edges.push({ id: a + '=>' + to.id, from: by[a], to, type: 'back', extra: true, claim: null, verdict: null, evidence: false });
  });

  return { root, nodes, edges, claims, by, L: leaves, md, N: nodes.length };
}
