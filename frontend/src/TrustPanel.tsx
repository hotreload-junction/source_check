import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import type { Ctx } from './Canvas';
import { useCompact } from './responsive';
import { CL, MONO, TH } from './theme';
import { barPos, criteria, type Cell } from './trust';

const card: CSSProperties = { padding: '14px 16px', borderRadius: TH.rad, background: TH.card, border: `1px solid ${TH.line}` };
const GRADIENT = `linear-gradient(90deg, ${CL.r}, ${CL.y} 50%, ${CL.g})`;
const SKELETON: CSSProperties = { background: `linear-gradient(90deg, ${TH.ink}14 0%, ${TH.ink}2b 50%, ${TH.ink}14 100%)`, backgroundSize: '400px 100%', animation: 'stShimmer 1.3s linear infinite' };


const hex = (a: number) => Math.round(a * 255).toString(16).padStart(2, '0');
const WORD = (s: number) => (s >= 0.67 ? 'strong' : s >= 0.34 ? 'moderate' : 'weak');

const IconOk = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
);
const IconWarn = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 9v4" /><path d="M12 17h.01" /><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
);

/** One cell: the whole column carries a light tint, the argument's strength deepens it. Side labels and icons sit once in the table header. */
function Td({ c, cell }: { c: string; cell: Cell }) {
  return (
    <td style={{ verticalAlign: 'top', padding: '10px 12px', background: c + hex(0.07 + cell.strength * 0.33), borderTop: `1px solid ${TH.line}`, borderLeft: `1px solid ${TH.line}`, fontSize: 12.5, lineHeight: 1.45 }}>
      {cell.items.length ? (
        <>
          {cell.items.map((t) => <div key={t} style={{ overflowWrap: 'anywhere', marginTop: 4 }}>{t}</div>)}
          <div style={{ marginTop: 5, fontFamily: MONO, fontSize: 10.5, fontWeight: 700, color: c }}>{WORD(cell.strength)}</div>
        </>
      ) : (
        <span style={{ color: TH.mut }}>–</span>
      )}
    </td>
  );
}

/** Phone layout of one cell: a stacked box with its side label instead of a table column. */
function CellBox({ c, name, icon, cell }: { c: string; name: string; icon: ReactNode; cell: Cell }) {
  return (
    <div style={{ borderRadius: 12, border: `1px solid ${TH.line}`, background: c + hex(0.07 + cell.strength * 0.33), padding: '10px 12px', fontSize: 12.5, lineHeight: 1.45 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, fontWeight: 700, color: c }}>
        <span style={{ width: 18, height: 18, borderRadius: 6, background: c, color: '#fff', display: 'inline-grid', placeItems: 'center', flex: 'none' }}>{icon}</span>
        {name}
        {cell.items.length > 0 && <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 10.5 }}>{WORD(cell.strength)}</span>}
      </div>
      {cell.items.length ? cell.items.map((t) => <div key={t} style={{ overflowWrap: 'anywhere', marginTop: 5 }}>{t}</div>) : <div style={{ marginTop: 4, color: TH.mut }}>–</div>}
    </div>
  );
}

/** Top bar: one big Propaganda <-> Objective slider; click opens a two-column table of what speaks for objectivity / propaganda per criterion. */
export function TrustBar({ ctx, ready, onToggle }: { ctx: Ctx; ready: boolean; onToggle?: () => void }) {
  const [open, setOpen] = useState(false);
  const compact = useCompact();
  const calc = ready && !!ctx.view.verdict && !!ctx.view.signals;
  const pos = useMemo(() => (calc ? barPos(ctx.view, ctx.tree) : null), [calc, ctx.view, ctx.tree]);
  const rows = useMemo(() => (calc ? criteria(ctx.view, ctx.tree) : null), [calc, ctx.view, ctx.tree]);
  const none = pos == null;
  const toggle = () => { if (calc) { setOpen((o) => !o); onToggle?.(); } };
  return (
    <div style={{ margin: compact ? '0 14px 10px' : '0 18px 12px', flex: 'none' }}>
      <div
        role="button"
        tabIndex={calc ? 0 : -1}
        aria-expanded={open}
        aria-busy={!calc}
        title={calc ? 'Show what speaks for objectivity and what for propaganda' : undefined}
        onClick={toggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }}
        style={{ ...card, display: 'flex', flexDirection: compact ? 'column' : 'row', alignItems: compact ? 'stretch' : 'center', gap: compact ? 8 : 14, padding: compact ? '10px 14px 12px' : '12px 16px', cursor: calc ? 'pointer' : 'default', boxShadow: TH.sh, scale: 'none' /* opt out of the global hover scale: a full-width bar would overflow */ }}
      >
        {compact && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, fontWeight: 700, fontSize: 12.5 }}>
            <span style={{ color: CL.r }}>Propaganda</span>
            <span style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 500, color: TH.mut, whiteSpace: 'nowrap' }}>{calc ? (open ? 'Hide ▴' : 'Why? ▾') : ''}</span>
            <span style={{ color: CL.g }}>Objective</span>
          </div>
        )}
        {!compact && <span style={{ fontWeight: 700, fontSize: 13, color: CL.r }}>Propaganda</span>}
        <div style={{ flex: 1, minWidth: 0, padding: compact ? '0 12px' : 0 }}>
          <div style={{ position: 'relative', height: 16, borderRadius: 99, background: none ? TH.line : GRADIENT, ...(calc ? {} : SKELETON) }}>
            {!none && (
              <div style={{ position: 'absolute', left: `${pos! * 100}%`, top: '50%', width: 28, height: 28, marginLeft: -14, marginTop: -14, boxSizing: 'border-box', borderRadius: '50%', background: TH.card, border: `4px solid ${TH.ink}`, boxShadow: '0 1px 5px #0004', transition: 'left .5s' }} />
            )}
          </div>
          {calc && none && <div style={{ marginTop: 5, textAlign: 'center', fontSize: 11.5, color: TH.mut }}>Insufficient evidence: the article has no position on the scale</div>}
        </div>
        {!compact && <span style={{ fontWeight: 700, fontSize: 13, color: CL.g }}>Objective</span>}
        {!compact && <span style={{ fontFamily: MONO, fontSize: 11.5, color: TH.mut, whiteSpace: 'nowrap', minWidth: 62, textAlign: 'right' }}>{calc ? (open ? 'Hide ▴' : 'Why? ▾') : ''}</span>}
      </div>

      {open && rows && (
        <div style={{ ...card, marginTop: 8, boxShadow: TH.sh, padding: 0, overflow: 'hidden' }}>
          {compact ? (
            <div>
              {rows.map((r, i) => (
                <div key={r.key} style={{ padding: '12px 12px 12px', borderTop: i ? `1px solid ${TH.line}` : undefined }}>
                  <div style={{ fontWeight: 700, fontSize: 13, lineHeight: 1.4 }}>{r.label}</div>
                  <div style={{ marginTop: 2, fontSize: 11.5, color: TH.mut, lineHeight: 1.4 }}>{r.basis}</div>
                  <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                    <CellBox c={CL.g} name="Objectivity" icon={<IconOk />} cell={r.pro} />
                    <CellBox c={CL.r} name="Propaganda" icon={<IconWarn />} cell={r.con} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
            <colgroup>
              <col style={{ width: '30%' }} />
              <col style={{ width: '35%' }} />
              <col style={{ width: '35%' }} />
            </colgroup>
            <thead>
              <tr>
                <th style={{ background: TH.card }} />
                <th style={{ textAlign: 'left', padding: '10px 12px', background: CL.g + hex(0.07), borderLeft: `1px solid ${TH.line}`, fontSize: 11.5, fontWeight: 700, color: CL.g }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <span style={{ width: 18, height: 18, borderRadius: 6, background: CL.g, color: '#fff', display: 'inline-grid', placeItems: 'center', flex: 'none' }}><IconOk /></span>
                    Objectivity
                  </span>
                </th>
                <th style={{ textAlign: 'left', padding: '10px 12px', background: CL.r + hex(0.07), borderLeft: `1px solid ${TH.line}`, fontSize: 11.5, fontWeight: 700, color: CL.r }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <span style={{ width: 18, height: 18, borderRadius: 6, background: CL.r, color: '#fff', display: 'inline-grid', placeItems: 'center', flex: 'none' }}><IconWarn /></span>
                    Propaganda
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <th scope="row" style={{ padding: '10px 12px', textAlign: 'left', verticalAlign: 'top', background: TH.card, borderTop: `1px solid ${TH.line}`, borderRight: `1px solid ${TH.line}`, fontSize: 12.5, lineHeight: 1.4 }}>
                    <div style={{ fontWeight: 700 }}>{r.label}</div>
                    <div style={{ marginTop: 3, fontSize: 11, fontWeight: 400, color: TH.mut }}>{r.basis}</div>
                  </th>
                  <Td c={CL.g} cell={r.pro} />
                  <Td c={CL.r} cell={r.con} />
                </tr>
              ))}
            </tbody>
          </table>
          )}
          <div style={{ padding: '8px 12px 10px', fontSize: 11.5, color: TH.mut, lineHeight: 1.4, borderTop: `1px solid ${TH.line}` }}>
            Colour strength = how strong the argument is. Context only: the marker follows the article verdict, and not-checkable claims never count against the article.
          </div>
        </div>
      )}
    </div>
  );
}
