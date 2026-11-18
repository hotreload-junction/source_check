import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './api';
import { Wordmark } from './Brand';
import { Canvas, graphSize, type CanvasHandle, type Ctx } from './Canvas';
import { Panel } from './Panel';
import { useCompact, useViewport } from './responsive';
import { TrustBar } from './TrustPanel';
import { ARTICLE_V, CL, CLAIM_V, TH, srcName } from './theme';
import { buildTree, emptyLive, fromLive, fromResult, type Live, type TEdge, type TNode } from './tree';
import type { Analysis, ClaimVerdict, DemoCase, Progress } from './types';

const STEP_MS = 1000;           // live run: one node per second
const CACHED_TOTAL_MS = 3000;   // precomputed result (demo, cache hit): whole reveal takes 3 s
const upsert = <T extends { id: string }>(list: T[], x: T) => {
  const i = list.findIndex((y) => y.id === x.id);
  if (i < 0) return [...list, x];
  const next = [...list];
  next[i] = x;
  return next;
};

export default function App({ initial }: { initial: { url?: string; demo?: string; question?: string; job?: string } }) {
  const compact = useCompact();
  const [vw, vh] = useViewport();
  const [url, setUrl] = useState('');
  const [question, setQuestion] = useState('');        // F6: the question of the analysis on screen
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [live, setLive] = useState<Live>(emptyLive);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rev, setRev] = useState(0);
  const [sel, setSel] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [dd, setDd] = useState<string | null>(null);
  const [ov, setOv] = useState<Record<string, ClaimVerdict>>({}); // local reviewer overrides, keyed by edge id
  const order = useRef(new Map<string, number>());
  const closeEvents = useRef<(() => void) | null>(null);
  const token = useRef(0);
  const canvas = useRef<CanvasHandle>(null);
  const focusRef = useRef<string | null>(null);
  focusRef.current = focus;

  const view = useMemo(() => (analysis?.result ? fromResult(analysis.result) : fromLive(live, url)), [analysis, live, url]);
  const tree = useMemo(() => buildTree(view, order.current), [view]);
  const N = tree.N;
  const nRef = useRef(N);
  nRef.current = N;
  const done = view.done && rev >= N;

  // progressive reveal, one node per step (as in the design); a precomputed result is spread over CACHED_TOTAL_MS
  const [cached, setCached] = useState(false);
  useEffect(() => {
    const ms = cached ? Math.max(40, Math.round(CACHED_TOTAL_MS / Math.max(nRef.current, 1))) : STEP_MS;
    const t = setInterval(() => setRev((r) => (r < nRef.current ? r + 1 : r)), ms);
    return () => clearInterval(t);
  }, [cached]);

  const fitSoon = useCallback((ms = 520) => {
    setTimeout(() => (focusRef.current ? canvas.current?.fitFocus(focusRef.current) : canvas.current?.fit()), ms);
  }, []);
  useEffect(() => { fitSoon(80); }, [tree.L, tree.md, fitSoon]);

  const reset = () => {
    closeEvents.current?.();
    closeEvents.current = null;
    token.current++;
    order.current = new Map();
    setAnalysis(null); setLive(emptyLive()); setProgress(null); setError(null);
    setRev(0); setSel(null); setFocus(null); setDd(null); setOv({}); setCached(false);
    focusRef.current = null;
    return token.current;
  };

  const follow = (jobId: string, tk: number) => {
    const ok = () => tk === token.current;
    closeEvents.current = api.events(jobId, {
      progress: (p) => { if (ok()) setProgress(p); },
      node: (n) => { if (ok() && n.type !== 'domain') setLive((l) => ({ ...l, nodes: upsert(l.nodes, n) })); },
      edge: (e) => { if (ok()) setLive((l) => ({ ...l, edges: upsert(l.edges, e) })); },
      claim: (c) => { if (ok()) setLive((l) => ({ ...l, claims: upsert(l.claims, c) })); },
      done: (a) => { if (ok()) setAnalysis(a); },
      error: (m) => { if (ok()) setError(m); },
    });
  };

  const load = (jobId: string, tk: number) =>
    api.get(jobId).then((a) => {
      if (tk !== token.current) return;
      if (a.status === 'done' && a.result) { setAnalysis(a); setCached(true); }
      else if (a.status === 'failed') setError(a.error || 'Analysis failed');
      else { setProgress(a.progress); follow(jobId, tk); }
    });

  const fail = (tk: number) => (e: unknown) => { if (tk === token.current) setError(String((e as Error).message || e)); };

  const pickDemo = (d: DemoCase) => {
    const tk = reset();
    setUrl(d.url); setQuestion('');
    load(d.job_id, tk).catch(fail(tk));
    fitSoon(80);
  };

  // force = bypass the URL cache and recompute every step (contract: POST /api/analyses {force: true}).
  // The demo buttons keep pointing at their precomputed job ids, so a forced run never replaces a demo case.
  const startUrl = (u: string, force: boolean, q = '') => {
    const tk = reset();
    setUrl(u); setQuestion(q);
    api.start(u, force, q || undefined)
      .then((r) => { if (tk !== token.current) return; return r.status === 'done' ? load(r.job_id, tk) : follow(r.job_id, tk); })
      .catch(fail(tk));
  };

  useEffect(() => {
    api.demo().then(
      (ds) => {
        if (initial.url || initial.job) return;
        const d = ds.find((x) => x.key === initial.demo) ?? ds[0];
        if (d) pickDemo(d);
      },
      (e) => { if (!initial.url && !initial.job) setError(`Backend unreachable (${e.message || e}). Run npm run dev:mock for mock data.`); },
    );
    if (initial.url) { startUrl(initial.url, false, (initial.question ?? '').trim()); }
    else if (initial.job) { const tk = reset(); load(initial.job, tk).catch(fail(tk)); }   // F9: an uploaded PDF's analysis
    return () => closeEvents.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- derived, override-aware helpers ----
  const ctx: Ctx = useMemo(() => {
    // the edge the CLAIM verdict sits on: the evidence page, or the only cited page (other pages carry their own check, not the claim's verdict)
    const judgedEdge = (n: TNode): TEdge | undefined => {
      const mine = tree.edges.filter((e) => e.from === n && e.type === 'cites');
      return mine.find((e) => e.evidence) ?? (mine.length === 1 && mine[0].verdict != null ? mine[0] : undefined);
    };
    const infra = view.signals?.infrastructure.reasons ?? [];
    return {
      tree, view,
      vis: (n) => n.kind === 'root' || n.ord <= rev,
      cld: (n) => (n.kind === 'root' ? view.done && rev >= N : n.kind === 'claim' ? view.done && n.desc.every((x) => x.ord <= rev - 1) : n.ord <= rev - 1),
      edgeVerdict: (e) => ov[e.id] ?? e.verdict,
      claimVerdict: (n) => { const e = judgedEdge(n); return (e && ov[e.id]) || n.claim!.verdict; },
      overridden: (e) => ov[e.id] != null && ov[e.id] !== e.verdict,
      reviewed: (n) => { const e = judgedEdge(n); return !!e && ov[e.id] != null && ov[e.id] !== e.verdict; },
      flagged: new Set(tree.nodes.filter((n) => n.source && n.source.domain && infra.some((r) => r.includes(n.source!.domain!))).map((n) => n.id)),
      articleColor: view.verdict ? ARTICLE_V[view.verdict.label].c : CL.p,
      // the ring counts CENTRAL claims only: a claim found through the reader's question never changes the verdict (F6)
      supported: tree.claims.filter((c) => c.claim!.central !== false && c.claim!.verdict === 'supported').length, // backend verdicts, not local review
      nCentral: tree.claims.filter((c) => c.claim!.central !== false).length,
      question: view.focus?.question ?? question,
      questionNote: view.focus?.note ?? null,
    };
  }, [tree, view, rev, N, ov, question]);

  const pickRoot = () => {
    if (!done) return;
    setSel((s) => (s === tree.root.id ? null : tree.root.id)); setFocus(null); setDd(null);
    focusRef.current = null;
    fitSoon();
  };
  const pickClaim = (id: string) => {
    const on = focus !== id;
    setFocus(on ? id : null); setSel(on ? id : null); setDd(null);
    focusRef.current = on ? id : null;
    fitSoon();
  };
  const pick = (n: TNode) => {
    if (n.kind === 'root') return pickRoot();
    if (n.kind === 'claim') return pickClaim(n.id);
    if (n.kind === 'nosrc') return pickClaim(n.fc!);
    setSel((s) => (s === n.id ? null : n.id)); setDd(null);
  };
  const closeSel = () => { setSel(null); fitSoon(); };
  const clearFocus = () => { setFocus(null); focusRef.current = null; fitSoon(); };
  const classify = (e: TEdge, v: ClaimVerdict) =>
    setOv((o) => { const next = { ...o }; if (v === e.verdict) delete next[e.id]; else next[e.id] = v; return next; });

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (dd) setDd(null);
      else if (sel || focus) { setSel(null); setFocus(null); focusRef.current = null; fitSoon(); }
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [dd, sel, focus, fitSoon]);

  const nC = ctx.nCentral;
  let status: string;
  if (error) status = error;
  else if (!view.done) status = progress ? `${progress.message} (${progress.pct}%)` : url ? 'Starting analysis …' : 'Loading …';
  else if (rev === 0) status = 'Reading the document …';
  else if (rev <= nC) status = `Extracting claim ${rev} of ${nC} …`;
  else if (!done) { const cur = tree.nodes.find((n) => n.ord === rev); status = cur?.source ? `Checking source: ${srcName(cur.source)} …` : 'Checking sources …'; }
  else status = '';

  const sn = sel ? tree.by[sel] ?? null : null;

  // phones: the detail panel is a full-screen sheet, so the page behind it must not scroll
  useEffect(() => {
    if (!compact || !sn) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [compact, sn]);

  const px = compact ? 14 : 18;
  // phones: the canvas is only as tall as the graph needs at the width-fitting zoom (a wide, flat tree leaves no dead space), at most 66 % of the screen
  const [gW, gH] = graphSize(tree);
  const canvasH = Math.round(Math.min(vh * 0.66, Math.max(340, gH * Math.min(1.3, Math.max(0.18, (vw - 36) / gW)) + 96)));
  const header = (
    <div style={{
      display: 'grid', gridTemplateColumns: compact ? 'auto 1fr' : '1fr auto 1fr', alignItems: 'center', justifyItems: 'start', gap: compact ? 8 : 14, flex: 'none',
      padding: compact ? `max(10px, env(safe-area-inset-top)) max(${px}px, env(safe-area-inset-right)) 8px max(${px}px, env(safe-area-inset-left))` : '10px 18px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <a href="#/" title="Back to start" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: compact ? '10px 14px' : '6px 12px', border: `1.5px solid ${TH.line}`, borderRadius: 8, background: TH.card, fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', textDecoration: 'none', color: TH.ink }}>← Back</a>
        <a href="#/methodology" title="How the verdicts are made" style={{ padding: compact ? '10px 8px' : '6px 8px', fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', textDecoration: 'none', color: TH.mut }}>How it works</a>
      </div>
      <a href="#/" title="Back to start" style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700, fontSize: 17, letterSpacing: '-.01em', textDecoration: 'none', color: TH.ink, justifySelf: compact ? 'end' : undefined }}>
        <Wordmark />
      </a>
      <div role="status" style={{ justifySelf: compact ? 'start' : 'end', gridColumn: compact ? '1 / -1' : undefined, maxWidth: compact ? undefined : 320, fontSize: 11.5, color: error ? CL.r : TH.mut, lineHeight: 1.35, textAlign: compact ? 'left' : 'right' }}>{status}</div>
    </div>
  );

  const legend = <Legend compact={compact} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', ...(compact ? { minHeight: '100dvh' } : { height: '100dvh' }), background: TH.bg, color: TH.ink, fontFamily: TH.font, fontSize: 14 }}>
      {header}

      <TrustBar ctx={ctx} ready={done} onToggle={() => fitSoon(80)} />

      <div style={{ display: 'flex', borderTop: `1px solid ${TH.line}`, ...(compact ? { flex: 'none', height: canvasH } : { flex: 1, minHeight: 480 }) }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Canvas ref={canvas} ctx={ctx} sel={sel} focus={focus} dd={dd} setDd={setDd} classify={classify} pick={pick} clearFocus={clearFocus} />
          {!compact && legend}
        </div>
        {!compact && (
          <div style={{ width: sn ? '50%' : '0px', flex: 'none', overflow: 'hidden', borderLeft: `1px solid ${TH.line}`, background: TH.bar, transition: 'width .45s cubic-bezier(.2,.7,.2,1)' }}>
            {sn && <Panel ctx={ctx} n={sn} pick={pick} close={closeSel} />}
          </div>
        )}
      </div>
      {compact && legend}

      {compact && sn && (
        <div role="dialog" aria-modal="true" aria-label="Details" style={{ position: 'fixed', inset: 0, zIndex: 60, background: TH.bar, animation: 'stSheetIn .22s ease-out' }}>
          <Panel ctx={ctx} n={sn} pick={pick} close={closeSel} compact />
        </div>
      )}
    </div>
  );
}

function Legend({ compact }: { compact: boolean }) {
  const colors = [
    { c: CLAIM_V.supported.c, t: 'supported' },
    { c: CLAIM_V.distorted.c, t: 'distorted' },
    { c: CL.r, t: 'contradicted · not mentioned · unsourced' },
    { c: CL.p, t: 'not checkable (neutral)' },
    { c: CL.o, t: 'source flagged by infrastructure signal' },
  ];
  const dot = (c: string) => <span style={{ flex: 'none', width: 10, height: 10, borderRadius: '50%', background: c }} />;
  const item = { display: 'flex', alignItems: 'center', gap: 6 } as const;
  if (compact) {
    return (
      <details style={{ flex: 'none', borderTop: `1px solid ${TH.line}`, background: TH.bar, fontSize: 12, color: TH.mut }}>
        <summary style={{ cursor: 'pointer', padding: '14px 14px', fontWeight: 600, fontSize: 13, color: TH.ink, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          Legend and help <span aria-hidden>▾</span>
        </summary>
        <div style={{ padding: '0 14px max(18px, env(safe-area-inset-bottom))', display: 'grid', gap: 9, lineHeight: 1.4 }}>
          <div style={{ fontWeight: 600, color: TH.ink }}>Color</div>
          {colors.map((l) => <span key={l.t} style={item}>{dot(l.c)}{l.t}</span>)}
          <div style={{ fontWeight: 600, color: TH.ink, marginTop: 4 }}>Line weight</div>
          <span style={item}><span style={{ flex: 'none', width: 26, height: 2, background: TH.mut, borderRadius: 2 }} />cited</span>
          <span style={item}><span style={{ flex: 'none', width: 26, height: 8, background: TH.mut, borderRadius: 3 }} />evidence for the verdict</span>
          <div style={{ marginTop: 4 }}>Tap a claim to show only its sources. The label on a line is the verdict for that claim-source pair; tap it to classify. Drag to move, pinch to zoom.{api.mock ? ' Mock data, fictional domains.' : ''}</div>
        </div>
      </details>
    );
  }
  return (
    <div style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 16, padding: '9px 18px', borderTop: `1px solid ${TH.line}`, fontSize: 11.5, color: TH.mut, background: TH.bar, flexWrap: 'wrap' }}>
      <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>Color</span>
      {colors.map((l) => <span key={l.t} style={{ ...item, whiteSpace: 'nowrap' }}>{dot(l.c)}{l.t}</span>)}
      <span style={{ width: 1, height: 14, background: TH.line }} />
      <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>Line weight</span>
      <span style={{ ...item, whiteSpace: 'nowrap' }}><span style={{ width: 26, height: 2, background: TH.mut, borderRadius: 2 }} />cited</span>
      <span style={{ ...item, whiteSpace: 'nowrap' }}><span style={{ width: 26, height: 8, background: TH.mut, borderRadius: 3 }} />evidence for the verdict</span>
      <span style={{ flex: 1 }} />
      <span style={{ whiteSpace: 'nowrap' }}>Click a claim to show only its sources · Label on line = verdict for that claim-source pair · Click it to classify{api.mock ? ' · Mock data, fictional domains' : ''}</span>
    </div>
  );
}
