import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type CSSProperties, type MouseEvent as RME, type PointerEvent as RPE, type ReactNode } from 'react';
import { Logo } from './Logo';
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
export const NEUTRAL = '#2b2b33';
const f1 = (v: number) => v.toFixed(1);
const skg = `linear-gradient(90deg, ${TH.ink}14 0%, ${TH.ink}2b 50%, ${TH.ink}14 100%)`;
const shimmer: CSSProperties = { background: skg, backgroundSize: '400px 100%', animation: 'stShimmer 1.3s linear infinite' };
const stop = (ev: { stopPropagation: () => void }) => ev.stopPropagation();
const KIND_SHORT = { link: 'Source', named: 'Named source', telegram: 'Telegram', social: 'Social media', self: 'Self-citation' } as const;

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
  const [zoom, setZoom] = useState(1);
  const zoomRef = useRef(1);
  const tx = useRef(0), ty = useRef(0);
  const gEl = useRef<HTMLDivElement | null>(null);
  const cEl = useRef<HTMLDivElement | null>(null);
  const pan = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const moved = useRef(false);

  const gw = Math.max(tree.L * 300 + 60, 680), gh = W0 * 2 + tree.md * CW;
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
    const z = zoomRef.current, nz = Math.min(2.2, Math.max(0.3, z * f));
    const px = cx ?? el.clientWidth / 2, py = cy ?? el.clientHeight / 2;
    tx.current = px - ((px - tx.current) * nz) / z;
    ty.current = py - ((py - ty.current) * nz) / z;
    setZ(nz);
  }, [setZ]);

  const fitBox = useCallback((x0: number, y0: number, x1: number, y1: number) => {
    const el = gEl.current;
    if (!el) return;
    const w = x1 - x0, h = y1 - y0, cw = el.clientWidth, ch = el.clientHeight;
    const nz = Math.max(0.4, Math.min(1.3, (cw - 120) / w, (ch - 120) / h));
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

  const mv = useCallback((e: PointerEvent) => {
    const p = pan.current;
    if (!p) return;
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    if (!moved.current && Math.hypot(dx, dy) < 5) return;
    moved.current = true;
    if (gEl.current) gEl.current.style.cursor = 'grabbing';
    if (cEl.current) cEl.current.style.transition = 'none';
    tx.current = p.tx + dx;
    ty.current = p.ty + dy;
    apply();
  }, [apply]);
  const up = useCallback(() => {
    window.removeEventListener('pointermove', mv);
    window.removeEventListener('pointerup', up);
    pan.current = null;
    if (gEl.current) gEl.current.style.cursor = 'grab';
    setTimeout(() => { moved.current = false; }, 0);
  }, [mv]);
  const down = (e: RPE) => {
    if (e.button || !gEl.current) return;
    pan.current = { x: e.clientX, y: e.clientY, tx: tx.current, ty: ty.current };
    moved.current = false;
    window.addEventListener('pointermove', mv);
    window.addEventListener('pointerup', up);
  };
  const capClick = (e: RME) => { if (moved.current) { e.stopPropagation(); e.preventDefault(); } };

  const nodes = tree.nodes.map((n) => {
    const [x, y] = pos(n);
    const vs = ctx.vis(n), sh = vs, fd = !inF(n), cl = ctx.cld(n);
    const root = n.kind === 'root', cm = n.kind === 'claim', stub = n.kind === 'nosrc', src = n.kind === 'source';
    const c = !cl ? CL.p : root ? ctx.articleColor : cm ? CLAIM_V[ctx.claimVerdict(n)].c : sourceColor(ctx, n);
    const load = !root && !cm && vs && !cl;
    const isSel = sel === n.id || (stub && sel === n.fc);
    let badge = '…';
    if (cl) {
      if (root) badge = `${ctx.supported}/${ctx.nCentral}`;
      else if (cm) badge = CLAIM_V[ctx.claimVerdict(n)].sym;
      else if (n.source) badge = ctx.flagged.has(n.id) ? '!' : n.source.fetch_status !== 'ok' ? '–' : `${Math.max(1, n.citedBy.length)}×`;
    }
    const meta = root ? `Document · ${ctx.view.article.domain}` : cm ? `Claim ${n.claimIdx}${n.claim!.focus ? ' · your question' : ''}${n.claim!.central === false ? ' · not central' : ''}${ctx.reviewed(n) ? ' · reviewed' : ''}` : stub ? `Claim ${tree.by[n.fc!].claimIdx}` : `${KIND_SHORT[n.source!.kind]}${n.source!.depth === 2 ? ' · depth 2' : ''}`;
    const ti = root ? ctx.view.article.title : cm ? n.claim!.text : stub ? 'No source given' : srcName(n.source!);
    const logoDomain = root ? ctx.view.article.domain : n.source ? n.source.domain : null;
    const glyph = root ? GLY.article : cm ? GLY.claim : stub ? GLY.none : GLY[n.source!.kind] ?? GLY.link;
    const lcol = root ? TH.bg : cm ? TH.ink : stub ? CL.r : pal(srcName(n.source!));
    const ls = root ? 56 : cm ? 40 : stub ? 28 : 64;
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
          padding: col ? '16px 12px 12px' : stub ? '8px 12px' : '10px 12px 10px 10px', justifyContent: col || stub ? 'center' : 'flex-start',
          display: 'flex', flexDirection: col ? 'column' : 'row', gap: col ? 8 : 12, alignItems: 'center', textAlign: col ? 'center' : 'left',
          boxShadow: isSel ? `0 0 0 4px ${c}66, 0 6px 18px #0002` : TH.sh,
          outline: cm && n.claim!.focus ? `2.5px dashed ${TH.ink}` : undefined, outlineOffset: 6, // F6: matches the reader's question
          transition: 'opacity .5s, transform .5s, border-color .4s, box-shadow .2s, scale .15s', zIndex: 3,
        }}
      >
        {!load ? (
          <>
            <Logo domain={logoDomain} glyph={glyph} color={lcol} size={ls} />
            <div style={{ flex: col ? 'none' : 1, minWidth: 0, width: col ? '100%' : undefined }}>
              <div style={{ fontSize: 11, opacity: 0.65, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{meta}</div>
              <div style={{ fontSize: col ? 16 : stub ? 14 : cm ? 15 : 13, fontWeight: 600, lineHeight: 1.25, marginTop: 2, display: '-webkit-box', WebkitLineClamp: root ? 8 : cm ? 4 : 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', overflowWrap: 'anywhere' }}>{ti}</div>
            </div>
            {!stub && (
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
                title={e.evidence && e.claim?.evidence ? `Quote match ${Math.round(e.claim.evidence.fuzzy_score)}` : ''}
                style={{
                  cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 5, background: cl ? TH.card : skg, backgroundSize: '400px 100%',
                  animation: cl ? 'none' : 'stShimmer 1.3s linear infinite', minWidth: cl ? 0 : 46, border: `2px solid ${c}`, borderRadius: 99,
                  padding: '1px 7px 1px 9px', fontFamily: MONO, fontSize: 11.5, fontWeight: 700, color: cl ? TH.ink : 'transparent', whiteSpace: 'nowrap',
                  transition: 'border-color .4s, scale .15s', boxShadow: open ? `0 0 0 3px ${c}55` : 'none',
                }}
              >
                {cl ? edgeLabel(ctx, e) : '…'}
                <span style={{ fontSize: 8, opacity: 0.7, display: 'inline-block', transform: `rotate(${open ? 180 : 0}deg)`, transition: 'transform .2s' }}>▼</span>
              </div>
              {open && cl && <EdgeCard ctx={ctx} e={e} c={c} up={up} classify={classify} pick={pick} />}
            </div>
          ))}
          {nodes}
        </div>
      </div>
      <div style={{ position: 'absolute', right: 14, bottom: 14, display: 'flex', alignItems: 'center', gap: 6, zIndex: 30 }}>
        <span style={{ fontSize: 11.5, color: TH.mut, marginRight: 8 }}>Drag to move · Ctrl + scroll to zoom</span>
        <Btn onClick={() => zoomBy(1 / 1.2)}>−</Btn>
        <div style={{ minWidth: 46, textAlign: 'center', fontFamily: MONO, fontSize: 12, fontWeight: 700 }}>{Math.round(zoom * 100)} %</div>
        <Btn onClick={() => zoomBy(1.2)}>+</Btn>
        <Btn onClick={fit}>Fit</Btn>
        {focus && (
          <div onClick={clearFocus} style={{ cursor: 'pointer', height: 34, padding: '0 12px', display: 'grid', placeItems: 'center', borderRadius: 10, background: TH.ink, color: TH.bg, fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap' }}>Show all claims</div>
        )}
      </div>
    </div>
  );
});

function Dot({ c, side }: { c: string; side: 'top' | 'bottom' }) {
  return <div style={{ position: 'absolute', left: '50%', [side]: -(TH.bw + 13), width: 26, height: 26, marginLeft: -13, borderRadius: '50%', background: c, border: `4px solid ${TH.bg}`, transition: 'background .4s', zIndex: 4, boxSizing: 'border-box' }} />;
}

function Btn({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <div onClick={onClick} style={{ cursor: 'pointer', minWidth: 34, height: 34, padding: '0 10px', display: 'grid', placeItems: 'center', borderRadius: 10, background: TH.card, border: `1.5px solid ${TH.line}`, fontWeight: 600, fontSize: 14, color: TH.ink, whiteSpace: 'nowrap', boxSizing: 'border-box' }}>{children}</div>
  );
}

export const cap: CSSProperties = { fontSize: 11, letterSpacing: '.07em', textTransform: 'uppercase', color: TH.mut, fontWeight: 600 };

// The only real per-edge number (docs/graph-view.md): rapidfuzz score of the evidence quote vs. the fetched source.
export function QuoteMatch({ score, threshold }: { score: number; threshold: number | null }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 34px', alignItems: 'center', gap: 10, fontSize: 12 }}>
      <div style={{ position: 'relative', height: 8, borderRadius: 4, background: TH.line }}>
        <div style={{ height: '100%', width: `${score}%`, borderRadius: 4, background: threshold != null && score < threshold ? CL.o : TH.ink }} />
        {threshold != null && <div title={`Threshold ${threshold}`} style={{ position: 'absolute', top: -3, bottom: -3, left: `${threshold}%`, width: 2, background: CL.r }} />}
      </div>
      <span style={{ fontFamily: MONO, fontWeight: 700, textAlign: 'right' }}>{Math.round(score)}</span>
    </div>
  );
}

function EdgeCard({ ctx, e, c, up, classify, pick }: { ctx: Ctx; e: TEdge; c: string; up: boolean; classify: (e: TEdge, v: ClaimVerdict) => void; pick: (n: TNode) => void }) {
  const claim = e.claim;
  const ev = e.evidence && claim?.evidence ? claim.evidence : null;
  const cur = ctx.edgeVerdict(e);
  const judged = e.type === 'cites' && e.verdict != null;
  const thr = ctx.view.audit?.fuzzy_threshold ?? null;
  return (
    <div
      onClick={stop}
      onPointerDown={stop}
      style={{
        position: 'absolute', left: '50%', transform: 'translateX(-50%)', top: up ? 'auto' : '100%', bottom: up ? '100%' : 'auto', margin: '9px 0', width: 330,
        background: TH.card, color: TH.ink, border: `${TH.bw}px solid ${c}`, borderRadius: TH.rad, boxShadow: '0 14px 36px #0003', padding: '14px 16px',
        fontFamily: TH.font, fontSize: 12.5, lineHeight: 1.4, cursor: 'default', userSelect: 'text', boxSizing: 'border-box',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: c }} />
        <span style={{ fontWeight: 700, fontSize: 14, flex: 1 }}>{edgeLabel(ctx, e)}</span>
        {ev && <span title="Quote match of the evidence quote against the fetched source text" style={{ fontFamily: MONO, fontWeight: 700, fontSize: 14 }}>{Math.round(ev.fuzzy_score)}</span>}
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
                <div key={v} onClick={(x) => { stop(x); classify(e, v); }} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 7, padding: '7px 9px', borderRadius: 10, border: `2px solid ${on ? CLAIM_V[v].c : TH.line}`, background: on ? CLAIM_V[v].c + '26' : 'transparent', fontWeight: 600, fontSize: 12 }}>
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
      {ev && (
        <div style={{ marginTop: 12 }}>
          <div style={{ color: TH.mut, fontSize: 12, marginBottom: 5 }}>Quote match vs. fetched source text{thr != null ? ` (threshold ${thr})` : ''}</div>
          <QuoteMatch score={ev.fuzzy_score} threshold={thr} />
        </div>
      )}
      <div onClick={(x) => { stop(x); pick(e.to.kind === 'nosrc' ? e.from : e.to); }} style={{ marginTop: 12, cursor: 'pointer', textAlign: 'center', padding: 7, borderRadius: 8, background: TH.ink, color: TH.bg, fontWeight: 600 }}>
        {e.to.kind === 'nosrc' ? 'Open claim' : 'Open source'}
      </div>
    </div>
  );
}
