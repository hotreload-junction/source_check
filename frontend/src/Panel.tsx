import type { CSSProperties, ReactNode } from 'react';
import { cap, edgeColor, edgeLabel, QuoteMatch, sourceColor, type Ctx } from './Canvas';
import { Logo } from './Logo';
import { locate, segments, type Mark } from './text';
import { ARTICLE_V, CL, CLAIM_V, FETCH, GLY, KIND, MONO, SUBTYPE, TH, fmtDate, pal } from './theme';
import type { TNode } from './tree';

const box: CSSProperties = { padding: '14px 16px', borderRadius: TH.rad, background: TH.card, border: `1px solid ${TH.line}` };

function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div style={cap}>{title}</div>
        <div style={{ flex: 1 }} />
        {right}
      </div>
      {children}
    </div>
  );
}

function Ring({ deg, c, text, label }: { deg: number; c: string; text: string; label: string }) {
  return (
    <div style={{ textAlign: 'center', flex: 'none', maxWidth: 110 }}>
      <div style={{ width: 58, height: 58, margin: '0 auto', borderRadius: '50%', background: `conic-gradient(${c} ${deg}deg, ${TH.line} 0)`, display: 'grid', placeItems: 'center' }}>
        <div style={{ width: 46, height: 46, borderRadius: '50%', background: TH.bar, display: 'grid', placeItems: 'center', fontFamily: MONO, fontWeight: 700, fontSize: text.length > 3 ? 13 : 16 }}>{text}</div>
      </div>
      <div style={{ fontSize: 11, marginTop: 4, fontWeight: 600 }}>{label}</div>
    </div>
  );
}

function Pill({ c, children }: { c: string; children: ReactNode }) {
  return <span style={{ fontFamily: MONO, fontWeight: 700, fontSize: 12, padding: '2px 9px', borderRadius: 99, border: `2px solid ${c}`, whiteSpace: 'nowrap' }}>{children}</span>;
}

function Row({ k, children, warn }: { k: string; children: ReactNode; warn?: boolean }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: 10, fontSize: 13, lineHeight: 1.45, padding: '5px 0', borderTop: `1px solid ${TH.line}` }}>
      <span style={{ color: TH.mut }}>{k}</span>
      <span style={{ color: warn ? CL.o : TH.ink, fontWeight: warn ? 600 : 400, overflowWrap: 'anywhere' }}>{children}</span>
    </div>
  );
}

const thrText = (thr: number | null) => `Quote match vs. fetched source text${thr != null ? ` (threshold ${thr})` : ''}`;

export function Panel({ ctx, n, pick, close }: { ctx: Ctx; n: TNode; pick: (n: TNode) => void; close: () => void }) {
  const { tree, view } = ctx;
  const root = n.kind === 'root', cm = n.kind === 'claim';
  const text = view.article.text;

  const claimMark = (c: TNode): Mark | null => {
    const span = locate(text, c.claim!.char_start, c.claim!.char_end, c.claim!.text);
    return span ? { start: span[0], end: span[1], c: CLAIM_V[ctx.claimVerdict(c)].c, id: c.id } : null;
  };
  const marks = (root ? tree.claims : cm ? [n] : []).map(claimMark).filter((m): m is Mark => !!m);

  const kids: TNode[] = root ? tree.claims : n.kids.filter((k) => k.kind !== 'nosrc');
  const extraKids = cm ? tree.edges.filter((e) => e.extra && e.from.id === n.id).map((e) => e.to) : [];

  let kind = 'Document', ti = view.article.title, meta = `${view.article.domain} · published ${fmtDate(view.article.published_at)}`;
  let ring: ReactNode, logo: ReactNode;
  if (root) {
    const total = tree.claims.length;
    const av = view.verdict ? ARTICLE_V[view.verdict.label] : null;
    ring = <Ring deg={total ? (ctx.supported / total) * 360 : 0} c={ctx.articleColor} text={`${ctx.supported}/${total}`} label={av ? av.label : 'Calculating'} />;
    logo = <Logo domain={view.article.domain} glyph={GLY.article} color={TH.ink} size={60} />;
  } else if (cm) {
    const v = ctx.claimVerdict(n), ev = n.claim!.evidence;
    kind = `Claim ${n.claimIdx}`;
    ti = n.claim!.text;
    meta = 'Extracted from the document' + (ctx.reviewed(n) ? ' · your review' : '');
    ring = ev
      ? <Ring deg={ev.fuzzy_score * 3.6} c={CLAIM_V[v].c} text={String(Math.round(ev.fuzzy_score))} label={CLAIM_V[v].label} />
      : <Ring deg={360} c={CLAIM_V[v].c} text={CLAIM_V[v].sym} label={CLAIM_V[v].label} />;
    logo = <Logo glyph={GLY.claim} color={TH.ink} size={60} />;
  } else {
    const s = n.source!;
    kind = n.kids.length ? 'Intermediate source' : s.depth === 2 ? 'Source of a source · origin' : 'Source';
    ti = s.domain;
    meta = `${KIND[s.kind]} · ${FETCH[s.fetch_status]}${s.fetched_via ? ' via ' + s.fetched_via.replace('_', '.') : ''}`;
    const cnt = Math.max(1, n.citedBy.length);
    ring = <Ring deg={360} c={sourceColor(ctx, n)} text={`${cnt}×`} label={n.citedBy.length > 1 ? `Cited by ${cnt} claims` : 'Cited once'} />;
    logo = <Logo domain={s.domain} glyph={GLY[s.kind]} color={pal(s.domain)} size={60} />;
  }

  return (
    <div style={{ width: '50vw', height: '100%', overflow: 'auto', padding: '20px 26px 40px', boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
        {logo}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={cap}>{kind}</div>
          <div style={{ fontSize: 19, fontWeight: 600, lineHeight: 1.25, letterSpacing: '-.01em', marginTop: 2, textWrap: 'pretty', overflowWrap: 'anywhere' } as CSSProperties}>{ti}</div>
          <div style={{ fontSize: 12.5, color: TH.mut, marginTop: 4 }}>{meta}</div>
        </div>
        {ring}
        <div onClick={close} style={{ cursor: 'pointer', flex: 'none', width: 28, height: 28, borderRadius: 7, border: `1px solid ${TH.line}`, display: 'grid', placeItems: 'center', fontSize: 14 }}>✕</div>
      </div>

      {root && <RootSections ctx={ctx} />}
      {cm && <ClaimSections ctx={ctx} n={n} />}
      {n.kind === 'source' && <SourceSections ctx={ctx} n={n} pick={pick} />}

      {(root || cm) && text && (
        <Section
          title="Source text"
          right={<><span style={{ width: 10, height: 10, borderRadius: 2, background: root ? TH.mut : CLAIM_V[ctx.claimVerdict(n)].c }} /><span style={{ fontSize: 11.5, color: TH.mut }}>{root ? 'Extracted claims, coloured by verdict' : 'Where the claim appears in the document'}</span></>}
        >
          <div style={{ ...box, padding: '16px 18px', fontSize: 14.5, lineHeight: 1.65, whiteSpace: 'pre-wrap', textWrap: 'pretty' } as CSSProperties}>
            {segments(text, marks).map((g, i) =>
              g.c ? (
                <span key={i} onClick={() => root && g.id && pick(tree.by[g.id])} style={{ background: g.c + '40', borderBottom: `2px solid ${g.c}`, borderRadius: 3, padding: '1px 0', fontWeight: 600, cursor: root ? 'pointer' : 'default' }}>{g.t}</span>
              ) : (
                <span key={i}>{g.t}</span>
              ),
            )}
          </div>
        </Section>
      )}

      <Section title={root ? 'Claims and their verdicts' : cm ? 'Sources for this claim' : n.kids.length ? 'Sources below' : 'No further sources. Origin reached.'}>
        <div style={{ display: 'grid', gap: 7 }}>
          {[...kids, ...extraKids].map((k) => <KidRow key={k.id} ctx={ctx} parent={n} k={k} pick={pick} />)}
          {cm && !kids.length && !extraKids.length && <div style={{ ...box, color: CL.r, fontWeight: 600, fontSize: 13 }}>No source given in the article.</div>}
        </div>
      </Section>

      {root && view.audit && (
        <div style={{ marginTop: 22, fontSize: 11.5, color: TH.mut, lineHeight: 1.5 }}>
          Audit: extract {view.audit.model_extract} · verify {view.audit.model_verify} · prompt {view.audit.prompt_version} · quote-match threshold {view.audit.fuzzy_threshold} · depth {view.audit.depth} · {view.audit.duration_s.toFixed(0)} s
        </div>
      )}
    </div>
  );
}

function KidRow({ ctx, parent, k, pick }: { ctx: Ctx; parent: TNode; k: TNode; pick: (n: TNode) => void }) {
  const e = ctx.tree.edges.find((x) => x.from.id === parent.id && x.to.id === k.id);
  let fx = '', c = TH.line;
  if (k.kind === 'claim') { const v = ctx.claimVerdict(k); fx = CLAIM_V[v].label; c = CLAIM_V[v].c; }
  else if (e) { fx = edgeLabel(ctx, e); c = edgeColor(ctx, e); }
  const dom = k.source?.domain ?? null;
  return (
    <div onClick={() => pick(k)} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 12, padding: '9px 12px', borderRadius: TH.rad, background: TH.card, border: `1px solid ${TH.line}` }}>
      <Logo domain={dom} glyph={k.source ? GLY[k.source.kind] : GLY.claim} color={dom ? pal(dom) : TH.ink} size={36} />
      <div style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 600, lineHeight: 1.3, overflowWrap: 'anywhere' }}>{k.kind === 'claim' ? k.claim!.text : dom}</div>
      <div style={{ fontSize: 11, color: TH.mut, fontFamily: MONO, whiteSpace: 'nowrap' }}>{fx}</div>
      <div style={{ width: 12, height: 12, borderRadius: '50%', background: c, flex: 'none' }} />
    </div>
  );
}

function RootSections({ ctx }: { ctx: Ctx }) {
  const { view } = ctx;
  if (!view.verdict || !view.signals) return <div style={{ ...box, marginTop: 20, color: TH.mut }}>The analysis is still running. The article verdict appears when all claims are checked.</div>;
  const av = ARTICLE_V[view.verdict.label];
  const sg = view.signals;
  const sig = (name: string, fired: boolean, detail: ReactNode) => (
    <div style={{ display: 'grid', gridTemplateColumns: '120px 90px 1fr', gap: 10, alignItems: 'start', fontSize: 13, lineHeight: 1.45, padding: '7px 0', borderTop: `1px solid ${TH.line}` }}>
      <span style={{ fontWeight: 600 }}>{name}</span>
      <span><Pill c={fired ? CL.r : TH.line}>{fired ? 'fired' : 'not fired'}</Pill></span>
      <span style={{ color: TH.mut }}>{detail}</span>
    </div>
  );
  return (
    <>
      <div style={{ ...box, marginTop: 20, border: `2px solid ${av.c}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: av.c }} />
          <span style={{ fontWeight: 700, fontSize: 14 }}>{av.label}</span>
        </div>
        <div style={{ marginTop: 8, fontSize: 13.5, lineHeight: 1.5 }}>{view.verdict.summary}</div>
      </div>
      <Section title="Signals">
        <div style={box}>
          {sig('Evidence', sg.evidence.fired, sg.evidence.detail)}
          {sig('Infrastructure', sg.infrastructure.fired, sg.infrastructure.reasons.length ? sg.infrastructure.reasons.join(' · ') : 'No shared analytics/AdSense IDs, no young source domains.')}
          {sig('Citation circle', sg.circle.fired, view.audit?.depth === 1 ? 'Needs depth 2; not run for this analysis.' : sg.circle.path ? sg.circle.path.join(' → ') : 'No source leads back to the article.')}
          <div style={{ marginTop: 8, fontSize: 12, color: TH.mut, lineHeight: 1.45 }}>
            A propaganda pattern needs at least two different signals. A single bad claim never produces it, and not-checkable claims never count against the article.
          </div>
        </div>
      </Section>
    </>
  );
}

function ClaimSections({ ctx, n }: { ctx: Ctx; n: TNode }) {
  const c = n.claim!, v = ctx.claimVerdict(n), ev = c.evidence;
  const evSrc = ev ? ctx.view.sources.find((s) => s.id === ev.source_id) : null;
  const thr = ctx.view.audit?.fuzzy_threshold ?? null;
  return (
    <>
      <div style={{ ...box, marginTop: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ ...cap, flex: 1 }}>Verdict</div>
          <Pill c={CLAIM_V[v].c}>{CLAIM_V[v].label}</Pill>
          {c.subtype && <span style={{ fontSize: 12, color: TH.mut }}>{SUBTYPE[c.subtype]}</span>}
        </div>
        <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.45, fontWeight: 500 }}>{ctx.view.done ? c.explanation : 'Still being checked …'}</div>
        {ctx.reviewed(n) && <div style={{ marginTop: 6, fontSize: 12, color: TH.mut }}>AI verdict: {CLAIM_V[c.verdict].label}. The colour shows your local review.</div>}
        {v === 'not_checkable' && <div style={{ marginTop: 6, fontSize: 12, color: TH.mut }}>Not checkable is neutral: it never counts against the article.</div>}
      </div>
      {ev && (
        <Section title="Evidence" right={<span style={{ fontSize: 11.5, color: TH.mut }}>verbatim from {evSrc?.domain ?? ev.source_id}</span>}>
          <div style={{ ...box, fontSize: 14, lineHeight: 1.55 }}>
            <span style={{ background: CLAIM_V[v].c + '40', borderBottom: `2px solid ${CLAIM_V[v].c}`, borderRadius: 3, fontWeight: 600 }}>“{ev.quote_original}”</span>
            {ev.quote_translation && <div style={{ marginTop: 8, fontSize: 13, color: TH.mut }}>{ev.language.toUpperCase()} → EN: “{ev.quote_translation}”</div>}
            <div style={{ marginTop: 12, fontSize: 12, color: TH.mut, marginBottom: 5 }}>{thrText(thr)}</div>
            <QuoteMatch score={ev.fuzzy_score} threshold={thr} />
          </div>
        </Section>
      )}
    </>
  );
}

function SourceSections({ ctx, n, pick }: { ctx: Ctx; n: TNode; pick: (n: TNode) => void }) {
  const s = n.source!;
  const d = ctx.view.domains.find((x) => x.domain === s.domain);
  const cluster = d?.cluster_id ? ctx.view.domains.filter((x) => x.cluster_id === d.cluster_id && x.domain !== d.domain) : [];
  const reasons = ctx.view.signals?.infrastructure.reasons.filter((r) => r.includes(s.domain)) ?? [];
  const links = ctx.tree.edges.filter((e) => e.to.id === n.id && e.type === 'cites');
  const passages = ctx.view.claims.filter((c) => c.evidence?.source_id === n.id);
  const thr = ctx.view.audit?.fuzzy_threshold ?? null;
  return (
    <>
      {links.map((e) => (
        <div key={e.id} style={{ ...box, marginTop: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div onClick={() => pick(e.from)} style={{ ...cap, flex: 1, cursor: 'pointer' }}>Link to “Claim {e.from.claimIdx}”</div>
            <Pill c={edgeColor(ctx, e)}>{edgeLabel(ctx, e)}</Pill>
          </div>
          <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.45 }}><span style={{ color: TH.mut }}>Claimed in the article:</span> {e.claim!.text}</div>
          {e.verdict != null
            ? <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.45, fontWeight: 500 }}>{e.claim!.explanation}</div>
            : <div style={{ marginTop: 6, fontSize: 13, color: TH.mut }}>Cited by the claim, but its verdict rests on another source.</div>}
          {e.evidence && e.claim!.evidence && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, color: TH.mut, marginBottom: 5 }}>{thrText(thr)}</div>
              <QuoteMatch score={e.claim!.evidence.fuzzy_score} threshold={thr} />
            </div>
          )}
        </div>
      ))}

      <Section title="Domain and infrastructure">
        <div style={box}>
          <Row k="Domain">{s.domain}</Row>
          {s.attribution_text && <Row k="Attribution in article">“{s.attribution_text}”</Row>}
          <Row k="Fetch">{FETCH[s.fetch_status]}{s.fetched_via ? ` via ${s.fetched_via.replace('_', '.')}` : ''}</Row>
          {d ? (
            <>
              <Row k="Registered">{fmtDate(d.created_at)}{d.age_days != null ? ` · ${d.age_days} days old` : ''}</Row>
              <Row k="Analytics IDs">{d.analytics_ids.length ? d.analytics_ids.join(', ') : 'none found'}</Row>
              <Row k="AdSense IDs">{d.adsense_ids.length ? d.adsense_ids.join(', ') : 'none found'}</Row>
              <Row k="Operator cluster" warn={cluster.length > 0}>{cluster.length ? `Shares IDs with ${cluster.map((x) => x.domain + (x.is_article_domain ? ' (the article)' : '')).join(', ')}` : 'No shared IDs with other domains'}</Row>
            </>
          ) : (
            <Row k="Domain data">{ctx.view.done ? 'Not collected for this domain' : 'Pending'}</Row>
          )}
          {reasons.map((r) => <Row key={r} k="Signal" warn>{r}</Row>)}
          {s.url && (
            <div style={{ marginTop: 10 }}>
              <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" style={{ fontSize: 13, fontWeight: 600 }}>Open original ↗</a>
            </div>
          )}
        </div>
      </Section>

      <Section title="Source text" right={<><span style={{ width: 10, height: 10, borderRadius: 2, background: passages.length ? CLAIM_V[passages[0].verdict].c : TH.line }} /><span style={{ fontSize: 11.5, color: TH.mut }}>Passage the claim relies on</span></>}>
        <div style={{ ...box, padding: '16px 18px', fontSize: 14.5, lineHeight: 1.65 }}>
          {passages.length ? passages.map((c) => {
            const col = CLAIM_V[c.verdict].c;
            return (
              <div key={c.id} style={{ marginBottom: 8 }}>
                <span style={{ background: col + '40', borderBottom: `2px solid ${col}`, borderRadius: 3, padding: '1px 0', fontWeight: 600 }}>“{c.evidence!.quote_original}”</span>
                {c.evidence!.quote_translation && <div style={{ fontSize: 13, color: TH.mut, marginTop: 4 }}>{c.evidence!.language.toUpperCase()} → EN: “{c.evidence!.quote_translation}”</div>}
              </div>
            );
          }) : (
            <span style={{ color: TH.mut, fontSize: 13.5 }}>No passage from this source was used as evidence. Full source texts are not stored in the result.</span>
          )}
        </div>
      </Section>
    </>
  );
}
