import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { Canvas, type CanvasHandle, type Ctx } from './Canvas';
import { Logo } from './Logo';
import { Panel } from './Panel';
import { TrustBar } from './TrustPanel';
import { ARTICLE_V, CL, CLAIM_V, GLY, MONO, TH, fmtDate, srcName } from './theme';
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

export default function App({ initial }: { initial: { url?: string; demo?: string; question?: string } }) {
  const [demos, setDemos] = useState<DemoCase[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [url, setUrl] = useState('');
  const [input, setInput] = useState('');
  const [qInput, setQInput] = useState('');            // F6: optional reader question, as typed
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
    setActive(d.key); setUrl(d.url); setInput(''); setQInput(''); setQuestion('');
    load(d.job_id, tk).catch(fail(tk));
    fitSoon(80);
  };

  // force = bypass the URL cache and recompute every step (contract: POST /api/analyses {force: true}).
  // The demo buttons keep pointing at their precomputed job ids, so a forced run never replaces a demo case.
  const startUrl = (u: string, force: boolean, q = '') => {
    const tk = reset();
    setActive(null); setUrl(u); setQuestion(q);
    api.start(u, force, q || undefined)
      .then((r) => { if (tk !== token.current) return; return r.status === 'done' ? load(r.job_id, tk) : follow(r.job_id, tk); })
      .catch(fail(tk));
  };

  const analyze = (ev: FormEvent) => {
    ev.preventDefault();
    const u = input.trim();
    if (u) startUrl(u, false, qInput.trim());
  };

  const running = !!url && !view.done && !error;
  const reanalyze = () => { if (url && !running) startUrl(url, true, question); };

  useEffect(() => {
    api.demo().then(
      (ds) => {
        setDemos(ds);
        if (initial.url) return;
        const d = ds.find((x) => x.key === initial.demo) ?? ds[0];
        if (d) pickDemo(d);
      },
      (e) => { if (!initial.url) setError(`Backend unreachable (${e.message || e}). Run npm run dev:mock for mock data.`); },
    );
    if (initial.url) { setInput(initial.url); setQInput(initial.question ?? ''); startUrl(initial.url, false, (initial.question ?? '').trim()); }
    return () => closeEvents.current?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- derived, override-aware helpers ----
  const ctx: Ctx = useMemo(() => {
    const judgedEdge = (n: TNode): TEdge | undefined => tree.edges.find((e) => e.from === n && e.type === 'cites' && e.verdict != null);
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
    };
  }, [tree, view, rev, N, ov]);

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
  const replay = () => { setRev(0); setSel(null); setDd(null); setFocus(null); focusRef.current = null; fitSoon(80); };
  const skip = () => setRev(N);
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: TH.bg, color: TH.ink, fontFamily: TH.font, fontSize: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '10px 18px', flexWrap: 'wrap', flex: 'none' }}>
        <a href="#/" title="Back to start" style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700, fontSize: 17, letterSpacing: '-.01em', marginRight: 6, textDecoration: 'none', color: TH.ink }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, width: 20 }}>
            <div style={{ height: 4, background: TH.ink, borderRadius: 1, opacity: 0.35 }} />
            <div style={{ height: 4, background: TH.ink, borderRadius: 1, opacity: 0.65, width: '80%' }} />
            <div style={{ height: 4, background: TH.ink, borderRadius: 1, width: '60%' }} />
          </div>
          Source Trace
        </a>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {demos.map((d) => (
            <div key={d.key} onClick={() => pickDemo(d)} style={{ cursor: 'pointer', padding: '6px 13px', borderRadius: 99, fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap', border: `1.5px solid ${TH.ink}`, background: active === d.key ? TH.ink : 'transparent', color: active === d.key ? TH.bg : TH.ink }}>{d.label}</div>
          ))}
        </div>
        <form onSubmit={analyze} style={{ display: 'flex', gap: 6, flex: '1 1 260px', minWidth: 220, maxWidth: 620 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Paste an article URL"
            type="url"
            style={{ flex: 1, minWidth: 0, padding: '6px 12px', borderRadius: 8, border: `1.5px solid ${TH.line}`, background: TH.card, font: 'inherit', fontSize: 13 }}
          />
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            maxLength={500}
            placeholder="Optional: your question"
            aria-label="Optional question about the article"
            title="Optional: what are you unsure about in this article? We find the sentence and check it too. The article verdict stays the same."
            style={{ flex: 1, minWidth: 0, padding: '6px 12px', borderRadius: 8, border: `1.5px solid ${TH.line}`, background: TH.card, font: 'inherit', fontSize: 13 }}
          />
          <button type="submit" style={{ cursor: 'pointer', padding: '6px 12px', border: `1.5px solid ${TH.ink}`, borderRadius: 8, font: 'inherit', fontWeight: 600, fontSize: 13, background: TH.ink, color: TH.bg }}>Analyze</button>
        </form>
        <div style={{ flex: 1 }} />
        {api.mock && <span style={{ fontFamily: MONO, fontSize: 11, padding: '3px 8px', borderRadius: 6, background: CL.y + '33', border: `1px solid ${CL.y}` }}>MOCK DATA</span>}
        <TopBtn onClick={reanalyze} disabled={!url || running} title="Ignore the cache and analyze this article again from scratch (takes 1-2 minutes)">Force re-analyze</TopBtn>
        <TopBtn onClick={replay}>Rebuild</TopBtn>
        <TopBtn onClick={skip}>Show instantly</TopBtn>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14, margin: '0 18px 10px', padding: '6px 14px', border: `${TH.bw}px solid ${TH.line}`, background: TH.card, borderRadius: TH.rad, boxShadow: TH.sh, flex: 'none' }}>
        <Logo domain={view.article.domain || null} glyph={GLY.article} color={TH.ink} size={32} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-.01em', lineHeight: 1.25, overflowWrap: 'anywhere' }}>{view.article.title || 'No article loaded'}</div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 5, fontSize: 12, color: TH.mut, flexWrap: 'wrap' }}>
            <span>{view.article.domain}{view.article.published_at ? ` · published ${fmtDate(view.article.published_at)}` : ''}</span>
            {url && <span style={{ fontFamily: MONO, fontSize: 11, padding: '2px 7px', border: `1px solid ${TH.line}`, borderRadius: 6, whiteSpace: 'nowrap', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' }}>{url}</span>}
          </div>
        </div>
        {status && <div style={{ width: 'min(280px,26%)', fontSize: 11.5, color: error ? CL.r : TH.mut, lineHeight: 1.35, textAlign: 'right' }}>{status}</div>}
      </div>

      <TrustBar ctx={ctx} ready={done} onToggle={() => fitSoon(80)} />

      {(question || view.focus) && <FocusBar question={view.focus?.question ?? question} note={view.focus?.note ?? null} status={view.focus?.status ?? null} />}

      <div style={{ flex: 1, minHeight: 480, display: 'flex', borderTop: `1px solid ${TH.line}` }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Canvas ref={canvas} ctx={ctx} sel={sel} focus={focus} dd={dd} setDd={setDd} classify={classify} pick={pick} clearFocus={clearFocus} />
          <div style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 16, padding: '9px 18px', borderTop: `1px solid ${TH.line}`, fontSize: 11.5, color: TH.mut, background: TH.bar, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>Color</span>
            {[
              { c: CLAIM_V.supported.c, t: 'supported' },
              { c: CLAIM_V.distorted.c, t: 'distorted' },
              { c: CL.r, t: 'contradicted · not mentioned · unsourced' },
              { c: CL.p, t: 'not checkable (neutral)' },
              { c: CL.o, t: 'source flagged by infrastructure signal' },
            ].map((l) => (
              <span key={l.t} style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}><span style={{ width: 10, height: 10, borderRadius: '50%', background: l.c }} />{l.t}</span>
            ))}
            <span style={{ width: 1, height: 14, background: TH.line }} />
            <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>Line weight</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}><span style={{ width: 26, height: 2, background: TH.mut, borderRadius: 2 }} />cited</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}><span style={{ width: 26, height: 8, background: TH.mut, borderRadius: 3 }} />evidence for the verdict</span>
            <span style={{ flex: 1 }} />
            <span style={{ whiteSpace: 'nowrap' }}>Click a claim to show only its sources · Label on line = verdict for that claim-source pair · Click it to classify{api.mock ? ' · Mock data, fictional domains' : ''}</span>
          </div>
        </div>
        <div style={{ width: sn ? '50%' : '0px', flex: 'none', overflow: 'hidden', borderLeft: `1px solid ${TH.line}`, background: TH.bar, transition: 'width .45s cubic-bezier(.2,.7,.2,1)' }}>
          {sn && <Panel ctx={ctx} n={sn} pick={pick} close={closeSel} />}
        </div>
      </div>
    </div>
  );
}

// F6: the reader's question and what became of it (the note is built by the backend from the verdicts)
function FocusBar({ question, note, status }: { question: string; note: string | null; status: 'found' | 'not_in_article' | 'failed' | null }) {
  const c = status === 'found' ? TH.ink : status ? CL.p : TH.line;
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, margin: '0 18px 12px', padding: '9px 16px', border: `2px dashed ${c}`, background: TH.card, borderRadius: TH.rad, flex: 'none', flexWrap: 'wrap' }}>
      <span style={{ fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', color: TH.mut, fontWeight: 600, whiteSpace: 'nowrap' }}>Your question</span>
      <span style={{ fontWeight: 600, fontSize: 14, overflowWrap: 'anywhere' }}>“{question}”</span>
      <span style={{ flex: '1 1 260px', fontSize: 12.5, color: TH.mut, lineHeight: 1.4 }}>{note ?? 'Finding the sentence your question is about …'}</span>
    </div>
  );
}

function TopBtn({ onClick, children, disabled, title }: { onClick: () => void; children: string; disabled?: boolean; title?: string }) {
  return <div title={title} aria-disabled={disabled || undefined} onClick={disabled ? undefined : onClick} style={{ cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.45 : 1, padding: '6px 12px', border: `1.5px solid ${TH.line}`, borderRadius: 8, fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', background: TH.card }}>{children}</div>;
}
