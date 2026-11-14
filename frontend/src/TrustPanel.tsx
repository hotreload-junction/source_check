import { useMemo, type CSSProperties } from 'react';
import { cap, type Ctx } from './Canvas';
import { CL, TH } from './theme';
import { trustProfile, type Gauge } from './trust';

const card: CSSProperties = { padding: '14px 16px', borderRadius: TH.rad, background: TH.card, border: `1px solid ${TH.line}` };

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
        style={{ position: 'relative', height: 10, margin: '12px 0 7px', borderRadius: 99, background: none ? TH.line : `linear-gradient(90deg, ${CL.r}, ${CL.y} 50%, ${CL.g})` }}
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

export function TrustSection({ ctx }: { ctx: Ctx }) {
  const t = useMemo(() => trustProfile(ctx.view, ctx.tree), [ctx.view, ctx.tree]);
  return (
    <div style={{ marginTop: 22 }}>
      <div style={cap}>Source trust</div>
      <div style={{ ...card, marginTop: 8 }}>
        <div style={{ display: 'grid', gap: 22, gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))' }}>
          <Slider title="Legitimacy of the sources" ends={['Doubtful', 'Established']} g={t.legitimacy} />
          <Slider title="Diversity of the sources" ends={['One origin', 'Many independent origins']} g={t.diversity} />
        </div>
        <div style={{ marginTop: 16, fontSize: 13.5, lineHeight: 1.5 }}>{t.summary}</div>
        <div style={{ marginTop: 12, display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))' }}>
          <Box c={CL.g} title="What is positive" mark="✓" items={t.positives} />
          <Box c={CL.r} title="What is negative" mark="!" items={t.negatives} />
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: TH.mut, lineHeight: 1.45 }}>
          Context from the origin sources' type, Wikipedia's Perennial Sources list and shared analytics IDs. It is not a verdict and does not change the article verdict.
        </div>
      </div>
    </div>
  );
}
