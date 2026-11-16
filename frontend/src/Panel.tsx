import type { CSSProperties, ReactNode } from 'react';
import { cap, edgeColor, edgeLabel, TONE, type Ctx } from './Canvas';
import { archiveText, cardLabels, reregisteredYears, rspLines, typeWord } from './labels';
import { Logo } from './Logo';
import { useCompact } from './responsive';
import { locate, safeHref, segments, type Mark } from './text';
import { ARTICLE_V, CL, CLAIM_V, FETCH, GLY, KIND, MONO, SUBTYPE, TH, fmtDate, pal, srcName } from './theme';
import { pairCheck, type TNode } from './tree';
import type { ClaimVerdict, Source } from './types';

const box: CSSProperties = { padding: '14px 16px', borderRadius: TH.rad, background: TH.card, border: `1px solid ${TH.line}` };

function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <div style={cap}>{title}</div>
        <div style={{ flex: 1 }} />
        {right}
      </div>
      {children}
    </div>
  );
}

function Ring({ deg, c, text, label }: { deg: number; c: string; text: string; label: string }) {
  const compact = useCompact();
  return (
    <div style={{ textAlign: 'center', flex: 'none', maxWidth: compact ? 84 : 110 }}>
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
  const compact = useCompact();
  return (
    <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '150px 1fr', gap: compact ? 2 : 10, fontSize: 13, lineHeight: 1.45, padding: compact ? '8px 0' : '5px 0', borderTop: `1px solid ${TH.line}` }}>
      <span style={{ color: TH.mut, fontSize: compact ? 12 : undefined }}>{k}</span>
      <span style={{ color: warn ? CL.o : TH.ink, fontWeight: warn ? 600 : 400, overflowWrap: 'anywhere' }}>{children}</span>
    </div>
  );
}


export function Panel({ ctx, n, pick, close, compact = false }: { ctx: Ctx; n: TNode; pick: (n: TNode) => void; close: () => void; compact?: boolean }) {
  const { tree, view } = ctx;
  const root = n.kind === 'root', cm = n.kind === 'claim';
  const text = view.article.text;
  const lg = compact ? 44 : 60;

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
    logo = <Logo domain={view.article.domain} glyph={GLY.article} color={TH.ink} size={lg} />;
  } else if (cm) {
    const v = ctx.claimVerdict(n);
    kind = `Claim ${n.claimIdx}`;
    ti = n.claim!.text;
    meta = 'Extracted from the document' + (ctx.reviewed(n) ? ' · your review' : '');
    ring = <Ring deg={360} c={CLAIM_V[v].c} text={CLAIM_V[v].sym} label={CLAIM_V[v].label} />;
    logo = <Logo glyph={GLY.claim} color={TH.ink} size={lg} />;
  } else {
    const s = n.source!;
    kind = n.kids.length ? 'Intermediate source' : s.depth >= 3 ? `Deeper source (depth ${s.depth}) · origin` : s.depth === 2 ? 'Source of a source · origin' : 'Source';
    ti = srcName(s);
    meta = `${KIND[s.kind]} · ${FETCH[s.fetch_status]}${s.fetched_via ? ' via ' + s.fetched_via.replace('_', '.') : ''}`;
    logo = <Logo domain={s.domain} glyph={GLY[s.kind]} color={pal(srcName(s))} size={lg} />;
  }

  return (
    <div style={{ width: '100%', height: '100%', overflow: 'auto', overscrollBehavior: 'contain', padding: compact ? '0 16px max(40px, env(safe-area-inset-bottom))' : '20px 26px 40px', boxSizing: 'border-box' }}>
      {compact && (
        <div style={{ position: 'sticky', top: 0, zIndex: 5, margin: '0 -16px', padding: 'max(8px, env(safe-area-inset-top)) 12px 8px', background: TH.bar, borderBottom: `1px solid ${TH.line}`, display: 'flex', alignItems: 'center', gap: 10 }}>
          <button type="button" onClick={close} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, padding: '10px 14px', border: `1.5px solid ${TH.ink}`, borderRadius: 10, background: TH.card, color: TH.ink, font: 'inherit', fontWeight: 600, fontSize: 14 }}>← Back to graph</button>
          <div style={{ ...cap, flex: 1, minWidth: 0, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{kind}</div>
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: compact ? 12 : 14, marginTop: compact ? 16 : 0 }}>
        {logo}
        <div style={{ flex: 1, minWidth: 0 }}>
          {!compact && <div style={cap}>{kind}</div>}
          <div style={{ fontSize: compact ? 17 : 19, fontWeight: 600, lineHeight: 1.25, letterSpacing: '-.01em', marginTop: 2, textWrap: 'pretty', overflowWrap: 'anywhere' } as CSSProperties}>{ti}</div>
          <div style={{ fontSize: 12.5, color: TH.mut, marginTop: 4, overflowWrap: 'anywhere' }}>{meta}</div>
        </div>
        {ring}
        {!compact && <div onClick={close} style={{ cursor: 'pointer', flex: 'none', width: 28, height: 28, borderRadius: 7, border: `1px solid ${TH.line}`, display: 'grid', placeItems: 'center', fontSize: 14 }}>✕</div>}
      </div>

      {root && <RootSections ctx={ctx} />}
      {cm && <ClaimSections ctx={ctx} n={n} />}
      {n.kind === 'source' && <SourceSections ctx={ctx} n={n} pick={pick} />}

      {(root || cm) && text && (
        <Section
          title="Source text"
          right={<><span style={{ width: 10, height: 10, borderRadius: 2, background: root ? TH.mut : CLAIM_V[ctx.claimVerdict(n)].c }} /><span style={{ fontSize: 11.5, color: TH.mut }}>{root ? 'Extracted claims, coloured by verdict' : 'Where the claim appears in the document'}</span></>}
        >
          <div style={{ ...box, padding: compact ? '12px 14px' : '16px 18px', fontSize: 14.5, lineHeight: 1.65, whiteSpace: 'pre-wrap', textWrap: 'pretty' } as CSSProperties}>
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
    <div onClick={() => pick(k)} style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 12, padding: '11px 12px', minHeight: 56, borderRadius: TH.rad, background: TH.card, border: `1px solid ${TH.line}` }}>
      <Logo domain={dom} glyph={k.source ? GLY[k.source.kind] : GLY.claim} color={dom ? pal(dom) : TH.ink} size={36} />
      <div style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 600, lineHeight: 1.3, overflowWrap: 'anywhere' }}>{k.kind === 'claim' ? k.claim!.text : dom}</div>
      <div style={{ fontSize: 11, color: TH.mut, fontFamily: MONO, textAlign: 'right', maxWidth: '38%' }}>{fx}</div>
      <div style={{ width: 12, height: 12, borderRadius: '50%', background: c, flex: 'none' }} />
    </div>
  );
}

function RootSections({ ctx }: { ctx: Ctx }) {
  const { view } = ctx;
  const compact = useCompact();
  if (!view.verdict || !view.signals) return <div style={{ ...box, marginTop: 20, color: TH.mut }}>The analysis is still running. The article verdict appears when all claims are checked.</div>;
  const av = ARTICLE_V[view.verdict.label];
  const sg = view.signals;
  const sig = (name: string, fired: boolean, detail: ReactNode) => (
    <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr auto' : '120px 90px 1fr', gap: compact ? '4px 10px' : 10, alignItems: 'start', fontSize: 13, lineHeight: 1.45, padding: compact ? '9px 0' : '7px 0', borderTop: `1px solid ${TH.line}` }}>
      <span style={{ fontWeight: 600 }}>{name}</span>
      <span style={compact ? { gridColumn: 2, gridRow: 1 } : undefined}><Pill c={fired ? CL.r : TH.line}>{fired ? 'fired' : 'not fired'}</Pill></span>
      <span style={{ color: TH.mut, overflowWrap: 'anywhere', ...(compact ? { gridColumn: '1 / -1' } : {}) }}>{detail}</span>
    </div>
  );
  const ai = view.overview?.source === 'ai';
  return (
    <>
      {/* 1. Summary (AI) */}
      <Section title="Summary" right={<span style={{ fontSize: 11.5, color: TH.mut }}>{ai ? 'AI-written from the verified results · never changes the verdict' : 'rule-based'}</span>}>
        <div style={{ ...box, border: `2px solid ${av.c}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: av.c }} />
            <span style={{ fontWeight: 700, fontSize: 14 }}>{av.label}</span>
          </div>
          <div style={{ marginTop: 8, fontSize: 14, lineHeight: 1.55 }}>{view.overview?.text ?? view.verdict.summary}</div>
        </div>
      </Section>
      {/* 2. Rules (fixed, code) */}
      <Section title="Rules">
        <div style={box}>
          {view.overview && <div style={{ fontSize: 13, lineHeight: 1.5, paddingBottom: 8 }}><b>Verdict rule:</b> {view.verdict.summary}</div>}
          {sig('Evidence', sg.evidence.fired, sg.evidence.detail)}
          {sig('Infrastructure', sg.infrastructure.fired, sg.infrastructure.reasons.length ? sg.infrastructure.reasons.join(' · ') : 'No shared analytics/AdSense IDs, no young source domains, no domain on a published network list.')}
          {sig('Citation circle', sg.circle.fired, view.audit?.depth === 1 ? 'Needs depth 2; not run for this analysis.' : sg.circle.path ? sg.circle.path.join(' → ') : 'No source leads back to the article.')}
          {sig('Laundering', !!sg.laundering?.fired, sg.laundering?.reasons.length ? sg.laundering.reasons.join(' · ') : 'The article does not republish Telegram or social-media channel posts as its source.')}
          <div style={{ marginTop: 8, fontSize: 12, color: TH.mut, lineHeight: 1.45 }}>
            A propaganda pattern needs at least two different signals. A single bad claim never produces it, and not-checkable claims never count against the article.
          </div>
        </div>
      </Section>
      {/* 3. Source (link; the text preview follows in "Source text") */}
      <Section title="Source">
        <div style={box}>
          <a href={safeHref(view.article.url)} target="_blank" rel="noopener noreferrer" style={linkStyle}>{view.article.domain} ↗</a>
          <div style={{ marginTop: 4, fontSize: 12.5, color: TH.mut, overflowWrap: 'anywhere' }}>{view.article.url}</div>
        </div>
      </Section>
    </>
  );
}

const linkStyle: CSSProperties = { color: TH.ink, fontWeight: 700, fontSize: 13, overflowWrap: 'anywhere' };

function SrcLink({ s, fallback }: { s?: Source | null; fallback: string }) {
  const name = s ? srcName(s) : fallback;
  const href = safeHref(s?.url);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" style={linkStyle}>{name} ↗</a> : <span style={linkStyle}>{name}</span>;
}

function ClaimSections({ ctx, n }: { ctx: Ctx; n: TNode }) {
  const c = n.claim!, v = ctx.claimVerdict(n), ev = c.evidence;
  const { view } = ctx;
  const src = (id: string) => view.sources.find((s) => s.id === id) ?? null;
  const evSrc = ev ? src(ev.source_id) : null;
  const found = new Set(c.found_source_ids ?? []);
  const others = (c.source_checks ?? []).filter((k) => !ev || k.source_id !== ev.source_id);
  const unread = [...c.source_ids, ...found].filter((id) => !ev || id !== ev.source_id).filter((id) => !others.some((k) => k.source_id === id));
  const how = (id: string) => (found.has(id) ? 'found by search' : 'cited by the article');
  const threshold = view.audit?.fuzzy_threshold ?? 90;
  return (
    <>
      {/* 1. Summary (AI) */}
      <Section title="Summary" right={c.overview && view.done ? <span style={{ fontSize: 11.5, color: TH.mut }}>AI-written from the checked pages</span> : undefined}>
        <div style={box}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ ...cap, flex: 1 }}>Verdict</div>
            <Pill c={CLAIM_V[v].c}>{CLAIM_V[v].label}</Pill>
            {c.subtype && <span style={{ fontSize: 12, color: TH.mut }}>{SUBTYPE[c.subtype]}</span>}
          </div>
          <div style={{ marginTop: 10, fontSize: 14, lineHeight: 1.5, fontWeight: 500 }}>{view.done ? (c.overview ?? c.explanation) : 'Still being checked …'}</div>
        </div>
      </Section>
      {/* 2. Rules (fixed, code) */}
      <Section title="Rules">
        <div style={box}>
          {c.overview && <Row k="Verdict basis">{c.explanation}</Row>}
          <Row k="Quote first">
            {ev
              ? `Verbatim quote found in the source (match ${ev.fuzzy_score.toFixed(0)}, needs ${threshold}).`
              : 'No verified quote: without one, a claim can never be "supported".'}
          </Row>
          <Row k="Source given">{c.source_ids.length ? 'The article cites a source for this claim.' : found.size ? 'The article cites nothing; the pages below were found by search.' : 'The article gives no source.'}</Row>
          {v === 'not_checkable' && <Row k="Neutral">Not checkable never counts against the article.</Row>}
          {c.check_text && <Row k="Checked as">“{c.check_text}” (the sentence needs its context; the claim above is shown as written)</Row>}
          {ctx.reviewed(n) && <Row k="Your review">AI verdict: {CLAIM_V[c.verdict].label}. The colour shows your local review.</Row>}
        </div>
      </Section>
      {c.focus && (
        <div style={{ ...box, marginTop: 12, border: `2px dashed ${TH.ink}` }}>
          <div style={cap}>Your question</div>
          {ctx.view.focus && <div style={{ marginTop: 8, fontSize: 13, fontWeight: 600, overflowWrap: 'anywhere' }}>“{ctx.view.focus.question}”</div>}
          <div style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.45, color: TH.mut }}>
            {c.central === false
              ? 'This sentence was checked because of your question. It is not one of the article\'s central claims, so it does not change the article verdict.'
              : 'This is one of the article\'s central claims, so it already counts for the article verdict.'}
          </div>
        </div>
      )}
      {/* 3. Source (link + preview: the verbatim passage, then every other page checked) */}
      <Section title="Source" right={<span style={{ fontSize: 11.5, color: TH.mut }}>link and the passage the verdict rests on</span>}>
        {ev && (
          <div style={{ ...box, marginBottom: 10 }}>
            <SrcLink s={evSrc} fallback={ev.source_id} />
            <span style={{ fontSize: 12, color: TH.mut }}> · {how(ev.source_id)}</span>
            <div style={{ marginTop: 8, fontSize: 14, lineHeight: 1.55 }}>
              <span style={{ background: CLAIM_V[v].c + '40', borderBottom: `2px solid ${CLAIM_V[v].c}`, borderRadius: 3, fontWeight: 600 }}>“{ev.quote_original}”</span>
              {ev.quote_translation && <div style={{ marginTop: 8, fontSize: 13, color: TH.mut }}>{ev.language.toUpperCase()} → EN: “{ev.quote_translation}”</div>}
            </div>
          </div>
        )}
        {others.map((k) => (
          <div key={k.source_id} style={{ ...box, marginBottom: 10 }}>
            <SrcLink s={src(k.source_id)} fallback={k.source_id} />
            <span style={{ fontSize: 12, color: TH.mut }}> · {how(k.source_id)}</span>
            <CheckBody k={{ verdict: k.verdict, quote: k.quote_original, translation: k.quote_translation, language: k.language, explanation: k.explanation, note: k.note, model: k.model, version: k.prompt_version }} />
          </div>
        ))}
        {unread.map((id) => (
          <div key={id} style={{ ...box, marginBottom: 10 }}>
            <SrcLink s={src(id)} fallback={src(id)?.attribution_text ?? id} />
            <span style={{ fontSize: 12, color: TH.mut }}> · {how(id)} · not read</span>
          </div>
        ))}
        {!ev && !others.length && !unread.length && <div style={{ ...box, color: TH.mut, fontSize: 13 }}>No source page for this claim.</div>}
      </Section>
    </>
  );
}

function SourceSections({ ctx, n, pick }: { ctx: Ctx; n: TNode; pick: (n: TNode) => void }) {
  const compact = useCompact();
  const s = n.source!;
  const d = s.domain ? ctx.view.domains.find((x) => x.domain === s.domain) : undefined;
  const cluster = d?.cluster_id ? ctx.view.domains.filter((x) => x.cluster_id === d.cluster_id && x.domain !== d.domain) : [];
  const reasons = ctx.view.signals?.infrastructure.reasons.filter((r) => !!s.domain && r.includes(s.domain)) ?? [];
  const links = ctx.tree.edges.filter((e) => e.to.id === n.id && e.type === 'cites');
  const passages = ctx.view.claims.filter((c) => c.evidence?.source_id === n.id);
  return (
    <>
      {links.map((e) => (
        <div key={e.id} style={{ ...box, marginTop: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div onClick={() => pick(e.from)} style={{ ...cap, flex: 1, cursor: 'pointer' }}>Link to “Claim {e.from.claimIdx}”</div>
            <Pill c={edgeColor(ctx, e)}>{edgeLabel(ctx, e)}</Pill>
          </div>
          <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.45 }}><span style={{ color: TH.mut }}>Claimed in the article:</span> {e.claim!.text}</div>
          {(() => {
            const k = e.claim && !e.evidence ? pairCheck(e.claim, n.id) : null;
            if (k) return <CheckBody k={{ verdict: k.verdict, quote: k.quote_original, translation: k.quote_translation, language: k.language, explanation: k.explanation, note: k.note, model: k.model, version: k.prompt_version }} />;
            return e.verdict != null
              ? <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.45, fontWeight: 500 }}>{e.claim!.explanation}</div>
              : <div style={{ marginTop: 6, fontSize: 13, color: TH.mut }}>Cited by the claim, but its verdict rests on another source.</div>;
          })()}
        </div>
      ))}

      {s.check && (
        <Section title="Check of this source" right={<span style={{ fontSize: 11.5, color: TH.mut }}>against the claim it is cited for · context, not part of the article verdict</span>}>
          <div style={box}>
            <CheckBody k={{ verdict: s.check.verdict, quote: s.check.quote_original, translation: null, language: null, explanation: s.check.explanation, note: null, model: s.check.model, version: s.check.prompt_version }} />
          </div>
        </Section>
      )}

      <Section title="Source type and reputation" right={<span style={{ fontSize: 11.5, color: TH.mut }}>Context only, never part of a verdict</span>}>
        <ReputationBox ctx={ctx} n={n} />
      </Section>

      <Section title="Domain and infrastructure">
        <div style={box}>
          <Row k="Domain">{s.domain ?? 'Unknown (named source without a link)'}</Row>
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
          {safeHref(s.url) && (
            <div style={{ marginTop: 10 }}>
              <a href={safeHref(s.url)} target="_blank" rel="noopener noreferrer nofollow" style={{ fontSize: 13, fontWeight: 600 }}>Open original ↗</a>
            </div>
          )}
        </div>
      </Section>

      <Section title="Source text" right={<><span style={{ width: 10, height: 10, borderRadius: 2, background: passages.length ? CLAIM_V[passages[0].verdict].c : TH.line }} /><span style={{ fontSize: 11.5, color: TH.mut }}>Passage the claim relies on</span></>}>
        <div style={{ ...box, padding: compact ? '12px 14px' : '16px 18px', fontSize: 14.5, lineHeight: 1.65 }}>
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

/** PLAN Q1/Q2: what kind of page this is and what dated public lists say about its domain. Restates backend fields;
 *  a missing field shows as "not listed" / "not checked", never as a guess. */
function ReputationBox({ ctx, n }: { ctx: Ctx; n: TNode }) {
  const s = n.source!;
  const d = s.domain ? ctx.view.domains.find((x) => x.domain === s.domain) : undefined;
  const tw = typeWord(s);
  const rows = d?.wikipedia_rsp ?? [];
  const rsp = cardLabels(d).find((l) => l.key === 'rsp');
  const yrs = reregisteredYears(d);
  const arch = archiveText(d);
  const pending = !ctx.view.done;
  return (
    <div style={box}>
      <Row k="Source type">
        {tw ?? (s.source_type === undefined ? 'Not classified in this result' : 'Unknown: no rule matched')}
        {s.source_type_reason && <div style={{ fontSize: 12, color: TH.mut, marginTop: 2 }}>{s.source_type_reason}</div>}
      </Row>
      {s.domain && (d ? (
        <>
          <Row k="Wikipedia rating">
            {rsp ? (
              <>
                <Pill c={TONE[rsp.tone]}>{rsp.text.replace('Wikipedia: ', '')}</Pill>
                {rspLines(rows).map((t) => <div key={t} style={{ fontSize: 12.5, marginTop: 4 }}>{t}</div>)}
                <div style={{ fontSize: 12, color: TH.mut, marginTop: 2 }}>Wikipedia Perennial Sources, snapshot {rows[0].snapshot}</div>
              </>
            ) : d.wikipedia_rsp ? 'Not on Wikipedia\'s Perennial Sources list (most sites are not; this is neutral)' : 'Not checked in this result'}
          </Row>
          <Row k="Popularity">
            {d.tranco_rank != null ? `Rank ${d.tranco_rank.toLocaleString('en-US')} on the Tranco top-sites list` : d.reputation_notes?.some((x) => x.startsWith('Not among')) ? 'Not among the 100,000 most visited sites (Tranco)' : 'Not checked in this result'}
            <div style={{ fontSize: 12, color: TH.mut, marginTop: 2 }}>Popularity, not reliability: widely read state media rank high too.</div>
          </Row>
          <Row k="Web archive" warn={yrs != null}>
            {arch ?? 'Not checked (archive lookup off or unavailable)'}
            {yrs != null && <div style={{ marginTop: 2 }}>Archived pages predate the current registration ({d.created_at!.slice(0, 10)}) by {yrs}+ year(s): the domain existed before and was registered again.</div>}
          </Row>
        </>
      ) : (
        <Row k="Reputation">{pending ? 'Pending' : 'Not collected for this domain'}</Row>
      ))}
    </div>
  );
}

/** One page's check against a claim: verdict, the page's own sentence (verbatim, quote first) and what produced it. */
function CheckBody({ k }: { k: { verdict: ClaimVerdict; quote: string | null; translation: string | null; language: string | null; explanation: string; note: string | null; model: string | null; version: string | null } }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
        <Pill c={CLAIM_V[k.verdict].c}>{CLAIM_V[k.verdict].label}</Pill>
        {k.verdict === 'not_checkable' && <span style={{ fontSize: 12, color: TH.mut }}>Neutral: never counts against the article.</span>}
      </div>
      {k.explanation && <div style={{ marginTop: 8, fontSize: 13, lineHeight: 1.45, fontWeight: 500 }}>{k.explanation}</div>}
      {k.note && k.note !== k.explanation && <div style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.45, color: TH.mut }}>{k.note}</div>}
      {k.quote && (
        <div style={{ marginTop: 8, fontSize: 13.5, lineHeight: 1.55 }}>
          <span style={{ background: CLAIM_V[k.verdict].c + '40', borderBottom: `2px solid ${CLAIM_V[k.verdict].c}`, borderRadius: 3, fontWeight: 600 }}>“{k.quote}”</span>
          {k.translation && <div style={{ marginTop: 4, fontSize: 12.5, color: TH.mut }}>{(k.language ?? '').toUpperCase()} → EN: “{k.translation}”</div>}
        </div>
      )}
      {k.model && <div style={{ marginTop: 6, fontSize: 11.5, color: TH.mut }}>Checked by {k.model} · prompt {k.version}</div>}
    </>
  );
}
