import { createPortal } from 'react-dom';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type CSSProperties, type MouseEvent as RME, type PointerEvent as RPE, type ReactNode } from 'react';
import { Logo } from './Logo';
import { cardLabels, typeWord, type Tone } from './labels';
import { useCompact } from './responsive';
import { CL, CLAIM_ORDER, CLAIM_V, GLY, MONO, TH, pal, srcName } from './theme';
import type { TEdge, TNode, Tree, View } from './tree';
import type { ClaimVerdict } from './types';

// Shared, override-aware view helpers (built in App).
export interface Ctx {
  tree: Tree;
  view: View;
  vis: (n: TNode) => boolean;
  cld: (n: TNode) => boolean;
  edgeVerdict: (e: TEdge) => ClaimVerdict | null; // with local reviewer override
  claimVerdict: (n: TNode) => ClaimVerdict; // with local reviewer override
  question?: string;                        // F6: the reader's question, shown in the root node
  questionNote?: string | null;             // F6: what became of it (backend note), shown as tooltip
  nCentral: number;                         // F6: claims that count for the article verdict (not found only via the question)
  overridden: (e: TEdge) => boolean;
  reviewed: (n: TNode) => boolean;
  flagged: Set<string>; // source ids named by the infrastructure signal
  articleColor: string;
  supported: number;
}

export interface CanvasHandle { fit(): void; fitFocus(id: string): void }

interface Props {
  ctx: Ctx;
  sel: string | null;
  focus: string | null;
  dd: string | null;
  setDd: (id: string | null) => void;
  classify: (e: TEdge, v: ClaimVerdict) => void;
  pick: (n: TNode) => void;
  clearFocus: () => void;
}

const CW = 240, W0 = 90;
const hh = (n: TNode) => (n.kind === 'root' ? 65 : n.kind === 'claim' ? 55 : n.kind === 'nosrc' ? 42 : 84);
const wd = (n: TNode) => (n.kind === 'root' ? 280 : n.kind === 'claim' ? 300 : n.kind === 'nosrc' ? 170 : 190);
/** Size of the graph in its own coordinates (also used by App to size the canvas on a phone). */
export const graphSize = (tree: Tree): [number, number] => [Math.max(tree.L * 300 + 60, 680), W0 * 2 + tree.md * CW];
export const NEUTRAL = '#2b2b33';
const f1 = (v: number) => v.toFixed(1);
const skg = `linear-gradient(90deg, ${TH.ink}14 0%, ${TH.ink}2b 50%, ${TH.ink}14 100%)`;
const shimmer: CSSProperties = { background: skg, backgroundSize: '400px 100%', animation: 'stShimmer 1.3s linear infinite' };
const stop = (ev: { stopPropagation: () => void }) => ev.stopPropagation();
const KIND_SHORT = { link: 'Source', named: 'Named source', telegram: 'Telegram', social: 'Social media', self: 'Self-citation' } as const;
export const TONE: Record<Tone, string> = { good: CL.g, warn: CL.o, bad: CL.r };

/** Card meta line: the Q1 source type replaces the generic "Source" for links; named sources keep their kind. */
function sourceMeta(s: NonNullable<TNode['source']>): string {
  const tw = typeWord(s);
  const head = s.kind === 'link' && tw ? tw : KIND_SHORT[s.kind] + (s.kind === 'named' && tw ? ` · ${tw.toLowerCase()}` : '');
  return head + (s.depth >= 2 ? ` · depth ${s.depth}` : '');
}

export function sourceColor(ctx: Ctx, n: TNode): string {
  if (n.kind === 'nosrc') return CL.r;
  if (!n.source) return NEUTRAL;
  if (ctx.flagged.has(n.id)) return CL.o;
  return n.source.fetch_status === 'ok' ? NEUTRAL : CL.p;
}

export function edgeLabel(ctx: Ctx, e: TEdge): string {
  if (e.type === 'nosrc') return 'No source';
  if (e.type === 'sub') return 'Cites';
  if (e.type === 'back') return 'Cites back';
  const v = ctx.edgeVerdict(e);
  return v ? CLAIM_V[v].label : 'Cited';
}

export function edgeColor(ctx: Ctx, e: TEdge): string {
  if (e.type === 'nosrc' || e.type === 'back') return CL.r;
  const v = e.type === 'cites' ? ctx.edgeVerdict(e) : null;
  return v ? CLAIM_V[v].c : CL.p;
}

export const Canvas = forwardRef<CanvasHandle, Props>(function Canvas({ ctx, sel, focus, dd, setDd, classify, pick, clearFocus }, ref) {
  const { tree } = ctx;
  const compact = useCompact();
  const compactRef = useRef(compact);
  compactRef.current = compact;
  const [zoom, setZoom] = useState(1);
  const zoomRef = useRef(1);
  const tx = useRef(0), ty = useRef(0);
  const gEl = useRef<HTMLDivElement | null>(null);
  const cEl = useRef<HTMLDivElement | null>(null);
  const pan = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const moved = useRef(false);

  const [gw, gh] = graphSize(tree);
  const pos = (n: TNode): [number, number] => [n.s * gw, W0 + n.d * CW];
  const inF = (n: TNode) => !focus || n.kind === 'root' || n.id === focus || n.fcs.has(focus);
  const dims = useRef<[number, number]>([gw, gh]);
  dims.current = [gw, gh];
  const treeRef = useRef(tree);
  treeRef.current = tree;

  const apply = useCallback(() => {
    const z = zoomRef.current;
    if (cEl.current) cEl.current.style.transform = `translate(${tx.current}px,${ty.current}px) scale(${z})`;
    if (gEl.current) {
      gEl.current.style.backgroundPosition = `${tx.current}px ${ty.current}px`;
      gEl.current.style.backgroundSize = `${TH.ds * z}px ${TH.ds * z}px`;
    }
  }, []);

  const setZ = useCallback((nz: number) => { zoomRef.current = nz; setZoom(nz); apply(); }, [apply]);

  const zoomBy = useCallback((f: number, cx?: number, cy?: number) => {
    const el = gEl.current;
    if (!el) return;
    const z = zoomRef.current, nz = Math.min(2.2, Math.max(compactRef.current ? 0.15 : 0.3, z * f));
    const px = cx ?? el.clientWidth / 2, py = cy ?? el.clientHeight / 2;
    tx.current = px - ((px - tx.current) * nz) / z;
    ty.current = py - ((py - ty.current) * nz) / z;
    setZ(nz);
  }, [setZ]);

  const fitBox = useCallback((x0: number, y0: number, x1: number, y1: number) => {
    const el = gEl.current;
    if (!el) return;
    const w = x1 - x0, h = y1 - y0, cw = el.clientWidth, ch = el.clientHeight;
    const pad = compactRef.current ? 36 : 120; // a phone cannot spare 120 px of margin
    const nz = Math.max(compactRef.current ? 0.18 : 0.4, Math.min(1.3, (cw - pad) / w, (ch - pad) / h));
    tx.current = (cw - w * nz) / 2 - x0 * nz;
    ty.current = (ch - h * nz) / 2 - y0 * nz;
    if (cEl.current) {
      cEl.current.style.transition = 'transform .6s cubic-bezier(.2,.7,.2,1)';
      setTimeout(() => { if (cEl.current) cEl.current.style.transition = ''; }, 700);
    }
    setZ(nz);
  }, [setZ]);

  const fit = useCallback(() => fitBox(0, 0, dims.current[0], dims.current[1]), [fitBox]);

  useImperativeHandle(ref, () => ({
    fit,
    fitFocus(id: string) {
      const w = dims.current[0];
      const ns = treeRef.current.nodes.filter((n) => n.id === id || n.fcs.has(id));
      if (!ns.length) return;
      const box = ns.map((n) => { const y = W0 + n.d * CW, x = n.s * w; return [x - wd(n) / 2, y - hh(n), x + wd(n) / 2, y + hh(n)]; });
      fitBox(Math.min(...box.map((b) => b[0])), Math.min(...box.map((b) => b[1])), Math.max(...box.map((b) => b[2])), Math.max(...box.map((b) => b[3])));
    },
  }), [fit, fitBox]);

  useEffect(() => { const t = setTimeout(fit, 150); return () => clearTimeout(t); }, [fit]);

  // wheel: pan, ctrl/cmd + wheel: zoom at cursor (non-passive so we can preventDefault)
  useEffect(() => {
    const el = gEl.current;
    if (!el) return;
    const wh = (e: WheelEvent) => {
      e.preventDefault();
      if (cEl.current) cEl.current.style.transition = 'none';
      if (e.ctrlKey || e.metaKey) {
        const r = el.getBoundingClientRect();
        zoomBy(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - r.left, e.clientY - r.top);
      } else {
        tx.current -= e.deltaX;
        ty.current -= e.deltaY;
        apply();
      }
    };
    el.addEventListener('wheel', wh, { passive: false });
    return () => el.removeEventListener('wheel', wh);
  }, [apply, zoomBy]);

  // One finger / mouse pans, two fingers pinch-zoom around their midpoint.
  const ptrs = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ d: number; z: number; cx: number; cy: number; tx: number; ty: number } | null>(null);
  const startPan = () => {
    const [p] = [...ptrs.current.values()];
    pan.current = p ? { x: p.x, y: p.y, tx: tx.current, ty: ty.current } : null;
  };
  const startPinch = () => {
    const el = gEl.current;
    const [a, b] = [...ptrs.current.values()];
    if (!el || !a || !b) return;
    const r = el.getBoundingClientRect();
    pan.current = null;
    pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, z: zoomRef.current, cx: (a.x + b.x) / 2 - r.left, cy: (a.y + b.y) / 2 - r.top, tx: tx.current, ty: ty.current };
    moved.current = true; // a pinch is never a tap on a card
  };
  const mv = useCallback((e: PointerEvent) => {
    if (!ptrs.current.has(e.pointerId)) return;
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (cEl.current && (pinch.current || moved.current)) cEl.current.style.transition = 'none';
    const g = pinch.current;
    if (g && ptrs.current.size >= 2 && gEl.current) {
      const [a, b] = [...ptrs.current.values()];
      const r = gEl.current.getBoundingClientRect();
      const nz = Math.min(2.2, Math.max(compactRef.current ? 0.15 : 0.3, (g.z * (Math.hypot(a.x - b.x, a.y - b.y) || 1)) / g.d));
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top;
      // the content point under the first midpoint follows the current midpoint
      tx.current = mx - ((g.cx - g.tx) * nz) / g.z;
      ty.current = my - ((g.cy - g.ty) * nz) / g.z;
      setZ(nz);
      return;
    }
    const p = pan.current;
    if (!p) return;
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    if (!moved.current && Math.hypot(dx, dy) < (e.pointerType === 'touch' ? 8 : 5)) return;
    moved.current = true;
    if (gEl.current) gEl.current.style.cursor = 'grabbing';
    if (cEl.current) cEl.current.style.transition = 'none';
    tx.current = p.tx + dx;
    ty.current = p.ty + dy;
    apply();
  }, [apply, setZ]);
  const up = useCallback((e: PointerEvent) => {
    ptrs.current.delete(e.pointerId);
    if (ptrs.current.size >= 2) { startPinch(); return; }
    pinch.current = null;
    if (ptrs.current.size === 1) { startPan(); return; } // one finger lifted after a pinch: keep panning without a jump
    window.removeEventListener('pointermove', mv);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', up);
    pan.current = null;
    if (gEl.current) gEl.current.style.cursor = 'grab';
    setTimeout(() => { moved.current = false; }, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mv]);
  const down = (e: RPE) => {
    if ((e.pointerType === 'mouse' && e.button) || !gEl.current) return;
    if (ptrs.current.size === 0) {
      moved.current = false;
      window.addEventListener('pointermove', mv);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    }
    ptrs.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (ptrs.current.size >= 2) startPinch(); else startPan();
  };
  const capClick = (e: RME) => { if (moved.current) { e.stopPropagation(); e.preventDefault(); } };

  const nodes = tree.nodes.map((n) => {
    const [x, y] = pos(n);
    const vs = ctx.vis(n), sh = vs, fd = !inF(n), cl = ctx.cld(n);
    const root = n.kind === 'root', cm = n.kind === 'claim', stub = n.kind === 'nosrc', src = n.kind === 'source';
    const c = !cl ? CL.p : root ? ctx.articleColor : cm ? CLAIM_V[ctx.claimVerdict(n)].c : sourceColor(ctx, n);
    const load = !root && !cm && vs && !cl;
    const isSel = sel === n.id || (stub && sel === n.fc);
    let badge = root ? '' : '…';
    if (cl) {
      if (cm) badge = CLAIM_V[ctx.claimVerdict(n)].sym;
      else if (n.source) badge = ctx.flagged.has(n.id) ? '!' : n.source.fetch_status !== 'ok' ? '–' : '';
    }
    const meta = root ? `Document · ${ctx.view.article.domain}` : cm ? `Claim ${n.claimIdx}${n.claim!.focus ? ' · your question' : ''}${n.claim!.central === false ? ' · not central' : ''}${ctx.reviewed(n) ? ' · reviewed' : ''}` : stub ? `Claim ${tree.by[n.fc!].claimIdx}` : sourceMeta(n.source!);
    const labels = src && n.source!.domain ? cardLabels(ctx.view.domains.find((d) => d.domain === n.source!.domain)) : [];
    const ti = root ? ctx.view.article.title : cm ? n.claim!.text : stub ? 'No source given' : srcName(n.source!);
    const logoDomain = root ? ctx.view.article.domain : n.source ? n.source.domain : null;
    const glyph = root ? GLY.article : cm ? GLY.claim : stub ? GLY.none : GLY[n.source!.kind] ?? GLY.link;
    const lcol = root ? TH.bg : cm ? TH.ink : stub ? CL.r : pal(srcName(n.source!));
    const ls = root ? 56 : cm ? 40 : stub ? 28 : labels.length ? 40 : 64; // labels need the room
    const pfg = root ? TH.bg : src && cl ? (c === CL.y || c === CL.p ? '#10141a' : '#ffffff') : TH.ink;
    const tint = cl ? (src ? c : c + '3a') : root ? TH.bg + '33' : TH.line;
    const col = src; // sources: centred column layout as in the design
    return (
      <div
        key={n.id}
        onClick={() => pick(n)}
        style={{
          position: 'absolute', left: f1(x) + 'px', top: f1(y) + 'px', width: wd(n), height: hh(n) * 2, boxSizing: 'border-box',
          transform: `translate(-50%,-50%) scale(${vs ? 1 : 0.85})`, opacity: sh ? (fd ? 0.5 : 1) : 0, pointerEvents: sh && cl ? 'auto' : 'none',
          cursor: 'pointer', background: root ? TH.ink : TH.card, color: root ? TH.bg : TH.ink,
          border: `${TH.bw}px ${load || stub || (cm && !cl) ? 'dashed' : 'solid'} ${c}`, borderRadius: TH.rad,
          padding: col ? (labels.length ? '12px 10px 10px' : '16px 12px 12px') : stub ? '8px 12px' : '10px 12px 10px 10px', justifyContent: col || stub ? 'center' : 'flex-start',
          display: 'flex', flexDirection: col ? 'column' : 'row', gap: col ? 8 : 12, alignItems: 'center', textAlign: col ? 'center' : 'left',
          boxShadow: isSel ? `0 0 0 4px ${c}66, 0 6px 18px #0002` : TH.sh,
          outline: cm && n.claim!.focus ? `2.5px dashed ${TH.ink}` : undefined, outlineOffset: 6, // F6: matches the reader's question
          transition: 'opacity .5s, transform .5s, border-color .4s, box-shadow .2s', zIndex: 3,
          scale: 'none', // opt out of the global hover scale: grown cards would overlap their neighbours
        }}
      >
        {!load ? (
          <>
            <Logo domain={logoDomain} glyph={glyph} color={lcol} size={ls} />
            <div style={{ flex: col ? 'none' : 1, minWidth: 0, width: col ? '100%' : undefined }}>
              <div style={{ fontSize: 11, opacity: 0.65, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{meta}</div>
              <div style={{ fontSize: col ? 16 : stub ? 14 : cm ? 15 : 13, fontWeight: 600, lineHeight: 1.25, marginTop: 2, display: '-webkit-box', WebkitLineClamp: root ? (ctx.question ? 4 : 8) : cm ? 4 : 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', overflowWrap: 'anywhere' }}>{ti}</div>
              {root && ctx.question && (
                <div title={ctx.questionNote ?? undefined} style={{ fontSize: 11.5, lineHeight: 1.3, marginTop: 6, paddingTop: 5, borderTop: `1px dashed ${TH.bg}66`, opacity: 0.85, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', overflowWrap: 'anywhere' }}>Your question: “{ctx.question}”</div>
              )}
              {labels.length > 0 && ( // PLAN Q2 reputation labels; hover for the list entry
                <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 4, marginTop: 6 }}>
                  {labels.map((l) => (
                    <span key={l.key} title={l.title} style={{ fontSize: 10.5, fontWeight: 700, lineHeight: '18px', padding: '0 8px', borderRadius: 99, background: TONE[l.tone], color: '#fff', whiteSpace: 'nowrap' }}>{l.text}</span>
                  ))}
                </div>
              )}
            </div>
            {!stub && badge && (
              <div style={{ flex: 'none', position: col ? 'absolute' : 'static', top: -14, right: -14, boxShadow: col ? `0 0 0 3px ${TH.card}` : 'none', width: 38, height: 38, borderRadius: '50%', background: tint, color: pfg, display: 'grid', placeItems: 'center', fontFamily: MONO, fontWeight: 700, fontSize: root ? 12 : 14, transition: 'background .4s' }}>{badge}</div>
            )}
          </>
        ) : (
          <>
            <div style={{ flex: 'none', width: ls, height: ls, borderRadius: 12, ...shimmer }} />
            <div style={{ flex: 1, minWidth: 0, width: '100%' }}>
              <div style={{ height: 9, width: '48%', margin: col ? '0 auto' : 0, borderRadius: 5, ...shimmer }} />
              <div style={{ height: 14, width: '92%', margin: col ? '10px auto 0' : '10px 0 0', borderRadius: 5, ...shimmer }} />
              <div style={{ height: 14, width: '62%', margin: col ? '6px auto 0' : '6px 0 0', borderRadius: 5, ...shimmer }} />
            </div>
            {!stub && <div style={{ flex: 'none', position: col ? 'absolute' : 'static', top: -14, right: -14, width: 38, height: 38, borderRadius: '50%', ...shimmer }} />}
          </>
        )}
        {!root && <Dot c={c} side="top" />}
        {n.kids.length > 0 && <Dot c={c} side="bottom" />}
      </div>
    );
  });

  const edges = tree.edges.map((e) => {
    const a = pos(e.from), b = pos(e.to);
    const vs = ctx.vis(e.from) && ctx.vis(e.to);
    const sh = vs, fd = !(inF(e.to) && inF(e.from) && (!focus || !e.extra || e.from.id === focus || e.from.fcs.has(focus)));
    const cl = ctx.cld(e.to) && (e.type !== 'cites' || ctx.view.done);
    const cm = e.type === 'claim';
    const c = cm || !cl ? CL.p : edgeColor(ctx, e);
    const downward = b[1] >= a[1];
    const y1 = downward ? a[1] + hh(e.from) : a[1] - hh(e.from);
    const y2 = downward ? b[1] - hh(e.to) : b[1] + hh(e.to);
    const mx = (y1 + y2) / 2;
    const w = cm ? 3 : e.type === 'back' ? 6 : e.evidence ? 8 : 3;
    const dashed = e.type === 'nosrc' || (e.extra && e.type !== 'back');
    return {
      e, c, cm, cl, sh, fd, mx, open: dd === e.id, up: mx > gh * 0.55, lx: (a[0] + b[0]) / 2,
      path: (
        <path
          key={e.id}
          d={`M${f1(a[0])} ${f1(y1)} C${f1(a[0])} ${f1(mx)}, ${f1(b[0])} ${f1(mx)}, ${f1(b[0])} ${f1(y2)}`}
          pathLength={1}
          style={{
            fill: 'none', stroke: c, strokeWidth: w, strokeLinejoin: 'round', strokeLinecap: dashed ? 'round' : undefined,
            strokeDasharray: dashed ? '0.015 0.03' : 1, strokeDashoffset: dashed || vs ? 0 : 1,
            strokeOpacity: sh ? (cl ? 0.85 : 0.5) * (fd ? 0.5 : 1) : 0, animation: cl || cm ? 'none' : 'stPulse 1.2s ease-in-out infinite',
            transition: 'stroke-dashoffset .9s ease, stroke .4s, stroke-width .4s, stroke-opacity .5s',
          }}
        />
      ),
    };
  });

  // Operator clusters (docs/graph-view.md): sources whose domains share an analytics/AdSense ID sit in a dashed box.
  const domOf = (n: TNode) => ctx.view.domains.find((d) => d.domain === n.source?.domain);
  const clusterIds = [...new Set(tree.nodes.map((n) => domOf(n)?.cluster_id).filter((k): k is string => !!k))];
  const boxes = clusterIds.flatMap((k) => {
    const ns = tree.nodes.filter((n) => domOf(n)?.cluster_id === k && ctx.vis(n) && inF(n));
    if (!ns.length) return [];
    const members = ctx.view.domains.filter((d) => d.cluster_id === k);
    const counts = new Map<string, number>();
    members.forEach((d) => [...d.analytics_ids, ...d.adsense_ids].forEach((i) => counts.set(i, (counts.get(i) ?? 0) + 1)));
    const shared = [...counts].filter(([, c]) => c > 1).map(([i]) => i);
    const P = 18;
    const x0 = Math.min(...ns.map((n) => pos(n)[0] - wd(n) / 2)) - P, x1 = Math.max(...ns.map((n) => pos(n)[0] + wd(n) / 2)) + P;
    const y0 = Math.min(...ns.map((n) => pos(n)[1] - hh(n))) - P, y1 = Math.max(...ns.map((n) => pos(n)[1] + hh(n))) + P;
    const text = `same operator${members.some((d) => d.is_article_domain) ? ' as the article' : ''}${shared.length ? ' · ' + shared.join(', ') : ''}`;
    return [{ k, x0, y0, w: x1 - x0, h: y1 - y0, text }];
  });

  return (
    <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
      <div
        ref={gEl}
        onPointerDown={down}
        onClickCapture={capClick}
        onClick={() => dd && setDd(null)}
        style={{
          position: 'absolute', inset: 0, overflow: 'hidden', cursor: 'grab', touchAction: 'none', userSelect: 'none',
          backgroundColor: TH.bg, backgroundImage: `radial-gradient(circle, ${TH.dot} ${TH.dr}px, transparent ${TH.dr2}px)`,
          backgroundSize: `${TH.ds * zoom}px ${TH.ds * zoom}px`, backgroundPosition: `${tx.current}px ${ty.current}px`,
        }}
      >
        <div ref={cEl} style={{ position: 'absolute', left: 0, top: 0, width: gw, height: gh, transform: `translate(${tx.current}px,${ty.current}px) scale(${zoom})`, transformOrigin: '0 0', willChange: 'transform' }}>
          {boxes.map((b) => (
            <div key={b.k} style={{ position: 'absolute', left: f1(b.x0) + 'px', top: f1(b.y0) + 'px', width: b.w, height: b.h, border: `2px dashed ${CL.o}`, borderRadius: TH.rad + 8, background: CL.o + '12', pointerEvents: 'none', zIndex: 1 }}>
              <span style={{ position: 'absolute', left: 14, bottom: -11, padding: '1px 9px', borderRadius: 99, background: TH.bg, border: `1.5px solid ${CL.o}`, fontFamily: MONO, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>{b.text}</span>
            </div>
          ))}
          <svg width={gw} height={gh} style={{ position: 'absolute', left: 0, top: 0, overflow: 'visible' }}>{edges.map((x) => x.path)}</svg>
          {edges.filter((x) => !x.cm).map(({ e, c, cl, sh, fd, open, up, mx, lx }) => (
            <div key={e.id} style={{ position: 'absolute', left: f1(lx) + 'px', top: f1(mx) + 'px', transform: 'translate(-50%,-50%)', opacity: sh ? (fd ? 0.5 : 1) : 0, pointerEvents: sh && cl ? 'auto' : 'none', transition: 'opacity .5s .5s', zIndex: open ? 20 : 2 }}>
              <div
                onClick={(ev) => { stop(ev); setDd(open ? null : e.id); }}
                style={{
                  cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, background: cl ? TH.card : skg, backgroundSize: '400px 100%',
                  animation: cl ? 'none' : 'stShimmer 1.3s linear infinite', minWidth: cl ? 0 : 46, border: `2px solid ${c}`, borderRadius: 99,
                  padding: compact ? '5px 10px 5px 12px' : '1px 7px 1px 9px', fontFamily: MONO, fontSize: compact ? 12.5 : 11.5, fontWeight: 700, color: cl ? TH.ink : 'transparent', whiteSpace: 'nowrap',
                  transition: 'border-color .4s', boxShadow: open ? `0 0 0 3px ${c}55` : 'none',
                  scale: 'none', // opt out of the global hover scale: grown pills would overlap their neighbours
                }}
              >
                {cl ? edgeLabel(ctx, e) : '…'}
                <span style={{ fontSize: 8, opacity: 0.7, display: 'inline-block', transform: `rotate(${open ? 180 : 0}deg)`, transition: 'transform .2s' }}>▼</span>
              </div>
              {open && cl && <EdgeCard ctx={ctx} e={e} c={c} up={up} compact={compact} close={() => setDd(null)} classify={classify} pick={pick} />}
            </div>
          ))}
          {nodes}
        </div>
      </div>
      {compact && focus && (
        <button type="button" onClick={clearFocus} style={{ cursor: 'pointer', position: 'absolute', left: 10, top: 10, zIndex: 30, height: 40, padding: '0 14px', border: 'none', borderRadius: 10, background: TH.ink, color: TH.bg, font: 'inherit', fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap' }}>← Show all claims</button>
      )}
      <div style={{ position: 'absolute', right: compact ? 10 : 14, bottom: compact ? 10 : 14, display: 'flex', alignItems: 'center', gap: compact ? 8 : 6, zIndex: 30 }}>
        {!compact && <span style={{ fontSize: 11.5, color: TH.mut, marginRight: 8 }}>Drag to move · Ctrl + scroll to zoom</span>}
        <Btn onClick={() => zoomBy(1 / 1.2)} label="Zoom out" big={compact}>−</Btn>
        <div style={{ minWidth: compact ? 44 : 46, textAlign: 'center', fontFamily: MONO, fontSize: 12, fontWeight: 700 }}>{Math.round(zoom * 100)} %</div>
        <Btn onClick={() => zoomBy(1.2)} label="Zoom in" big={compact}>+</Btn>
        <Btn onClick={fit} label="Fit graph to screen" big={compact}>Fit</Btn>
        {!compact && focus && (
          <div onClick={clearFocus} style={{ cursor: 'pointer', height: 34, padding: '0 12px', display: 'grid', placeItems: 'center', borderRadius: 10, background: TH.ink, color: TH.bg, fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap' }}>Show all claims</div>
        )}
      </div>
    </div>
  );
});

function Dot({ c, side }: { c: string; side: 'top' | 'bottom' }) {
  return <div style={{ position: 'absolute', left: '50%', [side]: -(TH.bw + 13), width: 26, height: 26, marginLeft: -13, borderRadius: '50%', background: c, border: `4px solid ${TH.bg}`, transition: 'background .4s', zIndex: 4, boxSizing: 'border-box' }} />;
}

function Btn({ onClick, children, label, big }: { onClick: () => void; children: ReactNode; label?: string; big?: boolean }) {
  const h = big ? 42 : 34;
  return (
    <button type="button" aria-label={label} onClick={onClick} onPointerDown={(e) => e.stopPropagation()} style={{ cursor: 'pointer', minWidth: h, height: h, padding: '0 10px', display: 'grid', placeItems: 'center', borderRadius: 10, background: TH.card, border: `1.5px solid ${TH.line}`, font: 'inherit', fontWeight: 600, fontSize: big ? 16 : 14, color: TH.ink, whiteSpace: 'nowrap', boxSizing: 'border-box' }}>{children}</button>
  );
}

export const cap: CSSProperties = { fontSize: 11, letterSpacing: '.07em', textTransform: 'uppercase', color: TH.mut, fontWeight: 600 };

function EdgeCard({ ctx, e, c, up, compact, close, classify, pick }: { ctx: Ctx; e: TEdge; c: string; up: boolean; compact: boolean; close: () => void; classify: (e: TEdge, v: ClaimVerdict) => void; pick: (n: TNode) => void }) {
  const claim = e.claim;
  const ev = e.evidence && claim?.evidence ? claim.evidence : null;
  const cur = ctx.edgeVerdict(e);
  const judged = e.type === 'cites' && e.verdict != null;
  // Desktop: a card hanging off the label, scaled with the graph. Phone: a bottom sheet in a portal, because
  // a fixed element inside the transformed graph would be positioned (and shrunk) relative to the graph.
  const card = (
    <div
      onClick={stop}
      onPointerDown={stop}
      style={compact ? {
        position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 80, maxHeight: '80dvh', overflowY: 'auto', overscrollBehavior: 'contain',
        background: TH.card, color: TH.ink, borderTop: `${TH.bw}px solid ${c}`, borderRadius: `${TH.rad}px ${TH.rad}px 0 0`, boxShadow: '0 -14px 36px #0003',
        padding: '16px 16px max(18px, env(safe-area-inset-bottom))', fontFamily: TH.font, fontSize: 13.5, lineHeight: 1.45, cursor: 'default', userSelect: 'text', boxSizing: 'border-box', animation: 'stSheetIn .2s ease-out',
      } : {
        position: 'absolute', left: '50%', transform: 'translateX(-50%)', top: up ? 'auto' : '100%', bottom: up ? '100%' : 'auto', margin: '9px 0', width: 330,
        background: TH.card, color: TH.ink, border: `${TH.bw}px solid ${c}`, borderRadius: TH.rad, boxShadow: '0 14px 36px #0003', padding: '14px 16px',
        fontFamily: TH.font, fontSize: 12.5, lineHeight: 1.4, cursor: 'default', userSelect: 'text', boxSizing: 'border-box',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: c }} />
        <span style={{ fontWeight: 700, fontSize: 14, flex: 1 }}>{edgeLabel(ctx, e)}</span>
        {compact && <button type="button" onClick={(x) => { stop(x); close(); }} aria-label="Close" style={{ cursor: 'pointer', flex: 'none', width: 36, height: 36, marginRight: -6, borderRadius: 8, border: `1px solid ${TH.line}`, background: TH.card, color: TH.ink, font: 'inherit', fontSize: 14 }}>✕</button>}
      </div>
      {claim && e.type !== 'sub' && e.type !== 'back' && <div style={{ marginTop: 8, color: TH.mut }}>Claim: <span style={{ color: TH.ink }}>{claim.text}</span></div>}
      {e.type === 'cites' && !judged && <div style={{ marginTop: 4 }}>The claim cites this source, but its verdict rests on another source.</div>}
      {(judged || e.type === 'nosrc') && claim?.explanation && <div style={{ marginTop: 4 }}>{claim.explanation}</div>}
      {e.type === 'sub' && <div style={{ marginTop: 8 }}>{e.from.source ? srcName(e.from.source) : 'A source'} cites {e.to.source ? srcName(e.to.source) : 'another source'}. Sources of sources are not judged per claim.</div>}
      {e.type === 'back' && <div style={{ marginTop: 8 }}>This citation leads back to the article or its operator cluster (citation circle).</div>}
      {ev && (
        <div style={{ marginTop: 10, padding: '8px 10px', borderRadius: 10, background: TH.bg }}>
          <div>“{ev.quote_original}”</div>
          {ev.quote_translation && <div style={{ marginTop: 4, color: TH.mut }}>{ev.language.toUpperCase()} → EN: “{ev.quote_translation}”</div>}
        </div>
      )}
      {e.type === 'cites' && (
        <>
          <div style={{ ...cap, fontSize: 10.5, marginTop: 12 }}>Classify this connection</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 7 }}>
            {CLAIM_ORDER.map((v) => {
              const on = v === cur;
              return (
                <div key={v} onClick={(x) => { stop(x); classify(e, v); }} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 7, padding: compact ? '10px 9px' : '7px 9px', borderRadius: 10, border: `2px solid ${on ? CLAIM_V[v].c : TH.line}`, background: on ? CLAIM_V[v].c + '26' : 'transparent', fontWeight: 600, fontSize: 12, minHeight: compact ? 44 : undefined }}>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: CLAIM_V[v].c, flex: 'none' }} />
                  {CLAIM_V[v].label}
                  <span style={{ marginLeft: 'auto', fontSize: 10, opacity: 0.65 }}>{v === e.verdict ? 'AI' : ''}</span>
                </div>
              );
            })}
          </div>
          <div style={{ marginTop: 6, fontSize: 11, color: TH.mut }}>{ctx.overridden(e) ? 'Your review is shown. ' : ''}Local review only; the article verdict comes from the backend.</div>
        </>
      )}
      <div onClick={(x) => { stop(x); pick(e.to.kind === 'nosrc' ? e.from : e.to); }} style={{ marginTop: 12, cursor: 'pointer', textAlign: 'center', padding: compact ? 12 : 7, borderRadius: 8, background: TH.ink, color: TH.bg, fontWeight: 600 }}>
        {e.to.kind === 'nosrc' ? 'Open claim' : 'Open source'}
      </div>
    </div>
  );
  if (!compact) return card;
  return createPortal(
    <>
      <div onClick={(x) => { stop(x); close(); }} onPointerDown={stop} style={{ position: 'fixed', inset: 0, zIndex: 79, background: '#0006', animation: 'stFade .2s ease-out' }} />
      {card}
    </>,
    document.body,
  );
}
