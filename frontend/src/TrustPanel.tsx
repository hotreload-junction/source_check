import { useMemo, useState, type CSSProperties } from 'react';
import { cap, type Ctx } from './Canvas';
import { CL, MONO, TH } from './theme';
import { overview, trustProfile, type Gauge } from './trust';

const card: CSSProperties = { padding: '14px 16px', borderRadius: TH.rad, background: TH.card, border: `1px solid ${TH.line}` };
const GRADIENT = `linear-gradient(90deg, ${CL.r}, ${CL.y} 50%, ${CL.g})`;

function Slider({ title, ends, g }: { title: string; ends: [string, string]; g: Gauge }) {
  const none = g.pos == null;
  return (
    <div title={g.basis}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{title}</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 12.5, fontWeight: 700, color: none ? TH.mut : TH.ink }}>{g.word}</span>
      </div>
      <div
        role="img"
        aria-label={`${title}: ${g.word}`}
        style={{ position: 'relative', height: 10, margin: '12px 0 7px', borderRadius: 99, background: none ? TH.line : GRADIENT }}
      >
        {!none && (
          <div style={{ position: 'absolute', left: `${g.pos! * 100}%`, top: '50%', width: 20, height: 20, marginLeft: -10, marginTop: -10, boxSizing: 'border-box', borderRadius: '50%', background: TH.card, border: `3.5px solid ${TH.ink}`, boxShadow: '0 1px 4px #0003' }} />
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: TH.mut }}>
        <span>{ends[0]}</span>
        <span>{ends[1]}</span>
      </div>
      <div style={{ marginTop: 6, fontSize: 11.5, color: TH.mut, lineHeight: 1.4 }}>{g.basis}</div>
    </div>
  );
}

function Box({ c, title, mark, items }: { c: string; title: string; mark: string; items: string[] }) {
  return (
    <div style={{ padding: '12px 14px', borderRadius: 14, background: c + '14', border: `1.5px solid ${c}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontWeight: 700, fontSize: 13 }}>
        <span style={{ width: 18, height: 18, borderRadius: '50%', background: c, color: '#fff', display: 'grid', placeItems: 'center', fontSize: 11 }}>{mark}</span>
        {title}
      </div>
      {items.length ? (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, lineHeight: 1.45 }}>
          {items.map((t) => <li key={t} style={{ marginTop: 3, overflowWrap: 'anywhere' }}>{t}</li>)}
        </ul>
      ) : (
        <div style={{ marginTop: 8, fontSize: 12.5, color: TH.mut }}>Nothing notable.</div>
      )}
    </div>
  );
}

/** Top bar: one big Propaganda <-> Objective slider; click to open the details (source trust sliders, summary, green/red boxes). */
export function TrustBar({ ctx, ready }: { ctx: Ctx; ready: boolean }) {
  const [open, setOpen] = useState(false);
  const calc = ready && !!ctx.view.verdict && !!ctx.view.signals;
  const tp = useMemo(() => (calc ? trustProfile(ctx.view, ctx.tree) : null), [calc, ctx.view, ctx.tree]);
  const ov = useMemo(() => (tp ? overview(ctx.view, ctx.tree, tp) : null), [tp, ctx.view, ctx.tree]);
  const none = !ov || ov.pos == null;
  const toggle = () => calc && setOpen((o) => !o);
  return (
    <div style={{ position: 'relative', margin: '0 18px 12px', flex: 'none' }}>
      <div
        role="button"
        tabIndex={calc ? 0 : -1}
        aria-expanded={open}
        title={calc ? 'Show what speaks for objectivity and what for propaganda' : undefined}
        onClick={toggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }}
        style={{ ...card, display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px', cursor: calc ? 'pointer' : 'default', boxShadow: TH.sh, scale: 'none' /* opt out of the global hover scale: a full-width bar would overflow */ }}
      >
        <span style={{ fontWeight: 700, fontSize: 13, color: CL.r }}>Propaganda</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ position: 'relative', height: 16, borderRadius: 99, background: none ? TH.line : GRADIENT }}>
            {!none && (
              <div style={{ position: 'absolute', left: `${ov!.pos! * 100}%`, top: '50%', width: 28, height: 28, marginLeft: -14, marginTop: -14, boxSizing: 'border-box', borderRadius: '50%', background: TH.card, border: `4px solid ${TH.ink}`, boxShadow: '0 1px 5px #0004', transition: 'left .5s' }} />
            )}
          </div>
          {(none || !calc) && <div style={{ marginTop: 5, textAlign: 'center', fontSize: 11.5, color: TH.mut }}>{calc ? 'Insufficient evidence: the article has no position on the scale' : 'Calculating …'}</div>}
        </div>
        <span style={{ fontWeight: 700, fontSize: 13, color: CL.g }}>Objective</span>
        <span style={{ fontFamily: MONO, fontSize: 11.5, color: TH.mut, whiteSpace: 'nowrap', minWidth: 62, textAlign: 'right' }}>{calc ? (open ? 'Hide ▴' : 'Why? ▾') : ''}</span>
      </div>

      {open && tp && ov && (
        <div style={{ ...card, position: 'absolute', left: 0, right: 0, top: 'calc(100% + 6px)', zIndex: 40, maxHeight: '62vh', overflow: 'auto', boxShadow: '0 12px 36px #0003' }}>
          <div style={cap}>Source trust</div>
          <div style={{ marginTop: 10, display: 'grid', gap: 22, gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))' }}>
            <Slider title="Legitimacy of the sources" ends={['Doubtful', 'Established']} g={tp.legitimacy} />
            <Slider title="Diversity of the sources" ends={['One origin', 'Many independent origins']} g={tp.diversity} />
          </div>
          <div style={{ marginTop: 16, fontSize: 13.5, lineHeight: 1.5 }}>{tp.summary}</div>
          <div style={{ marginTop: 12, display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
            <Box c={CL.g} title="What speaks for objectivity" mark="✓" items={ov.positives} />
            <Box c={CL.r} title="What speaks for propaganda" mark="!" items={ov.negatives} />
          </div>
          <div style={{ marginTop: 10, fontSize: 12, color: TH.mut, lineHeight: 1.45 }}>
            The position follows the article verdict, shifted by the share of supported claims. The lists restate verdicts, fired signals and source facts; they are context. Not-checkable claims never count against the article, and a propaganda pattern needs at least two different signals.
          </div>
        </div>
      )}
    </div>
  );
}
