import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { api } from './api';
import { Wordmark } from './Brand';
import { hasRun, label, pct, total } from './evalstats';
import { useCompact } from './responsive';
import { CL, MONO, TH } from './theme';
import type { Confusion, Methodology as M } from './types';

const card: CSSProperties = { background: TH.card, border: `1px solid ${TH.line}`, borderRadius: TH.rad, padding: '18px 20px', boxShadow: TH.sh };
const cap: CSSProperties = { fontSize: 11, letterSpacing: '.07em', textTransform: 'uppercase', color: TH.mut, fontWeight: 600 };

function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section style={{ marginTop: 32 }}>
      <h2 style={{ fontSize: 22, letterSpacing: '-.02em', margin: 0 }}>{title}</h2>
      {sub && <p style={{ color: TH.mut, fontSize: 14, lineHeight: 1.5, margin: '6px 0 0', maxWidth: 680 }}>{sub}</p>}
      <div style={{ marginTop: 16 }}>{children}</div>
    </section>
  );
}

function Metric({ name, value, note, strong }: { name: string; value: string; note?: string; strong?: boolean }) {
  const none = value === 'no run yet';
  return (
    <div style={{ ...card, flex: '1 1 140px', minWidth: 0, borderColor: strong ? TH.ink : TH.line, borderWidth: strong ? 2 : 1 }}>
      <div style={cap}>{name}</div>
      <div style={{ fontFamily: MONO, fontWeight: 700, fontSize: none ? 16 : 28, marginTop: 8, color: none ? TH.mut : TH.ink }}>{value}</div>
      {note && <div style={{ fontSize: 12, color: TH.mut, marginTop: 6, lineHeight: 1.4 }}>{note}</div>}
    </div>
  );
}

// Rows = truth, columns = our prediction. "Positive" is the class the section names.
function Matrix({ c, positive, negative }: { c: Confusion; positive: string; negative: string }) {
  const cell = (n: number, tag: string, good: boolean): ReactNode => (
    <div style={{ padding: '14px 10px', textAlign: 'center', background: good ? CL.g + '1f' : CL.r + '1f', borderRadius: 10 }}>
      <div style={{ fontFamily: MONO, fontWeight: 700, fontSize: 22 }}>{n}</div>
      <div style={{ fontSize: 11, color: TH.mut, marginTop: 2 }}>{tag}</div>
    </div>
  );
  const head: CSSProperties = { ...cap, textAlign: 'center', alignSelf: 'end' };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(70px, 110px) 1fr 1fr', gap: 6, alignItems: 'stretch', maxWidth: 420 }}>
      <div />
      <div style={head}>flagged: {positive}</div>
      <div style={head}>flagged: {negative}</div>
      <div style={{ ...cap, alignSelf: 'center' }}>truth: {positive}</div>
      {cell(c.tp, 'true positive', true)}
      {cell(c.fn, 'false negative', false)}
      <div style={{ ...cap, alignSelf: 'center' }}>truth: {negative}</div>
      {cell(c.fp, 'false positive', false)}
      {cell(c.tn, 'true negative', true)}
    </div>
  );
}

export function Methodology() {
  const [m, setM] = useState<M | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const compact = useCompact();
  useEffect(() => { api.methodology().then(setM, (e) => setErr(String(e.message || e))); }, []);

  const run = m ? hasRun(m.pairs.confusion) || hasRun(m.articles.confusion) : false;

  return (
    <div style={{ minHeight: '100dvh', background: TH.bg, color: TH.ink, fontFamily: TH.font, backgroundImage: `radial-gradient(${TH.dot} ${TH.dr}px, transparent ${TH.dr}px)`, backgroundSize: `${TH.ds}px ${TH.ds}px` }}>
      <div style={{ maxWidth: 880, margin: '0 auto', padding: compact ? 'max(18px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) 60px max(16px, env(safe-area-inset-left))' : '28px 20px 80px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <a href="#/" style={{ fontWeight: 700, fontSize: 17, letterSpacing: '-.01em', textDecoration: 'none', color: TH.ink }}><Wordmark /></a>
          <span style={{ color: TH.mut }}>/</span>
          <span style={{ fontWeight: 600, fontSize: 15 }}>Methodology</span>
        </div>

        <h1 style={{ fontSize: 'clamp(30px, 5vw, 46px)', lineHeight: 1.08, letterSpacing: '-.03em', margin: compact ? '28px 0 12px' : '48px 0 12px', maxWidth: 700 }}>How good is the detection? Measured, including its mistakes.</h1>
        <p style={{ fontSize: compact ? 15.5 : 17, lineHeight: 1.5, color: TH.mut, margin: 0, maxWidth: 680 }}>
          Every number below comes from a labelled evaluation run, not from the three demo articles. Thresholds were set on one article set and measured on another, so the false-positive rate on wire-service articles is honest.
        </p>

        {err && <div style={{ ...card, marginTop: 28, borderColor: CL.r, color: CL.r, fontWeight: 600 }}>Could not load the metrics ({err}).</div>}
        {!m && !err && <div style={{ marginTop: 28, color: TH.mut }}>Loading …</div>}

        {m && (
          <>
            {!run && (
              <div style={{ ...card, marginTop: 28, borderColor: CL.y }}>
                <b>The evaluation has not run yet.</b> The numbers appear here as soon as <span style={{ fontFamily: MONO, fontSize: 13 }}>eval/results/metrics.json</span> exists. We show an empty state instead of zeros so nobody mistakes missing data for a result.
              </div>
            )}

            <Section title="Claim check: does the source support the claim?" sub={`${m.pairs.n} labelled claim-source pairs, ${m.pairs.n_cross_language} of them across languages. The positive class is "not supported" (distorted, contradicted, not mentioned), because that is what we must catch.`}>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <Metric name="Precision" value={pct(m.pairs.precision, m.pairs.confusion)} note="Of the claims we flag as not supported, how many truly are." />
                <Metric name="Recall" value={pct(m.pairs.recall, m.pairs.confusion)} note="Of the claims that truly are not supported, how many we catch." />
                <Metric name="Pairs" value={String(m.pairs.n)} note={`${m.pairs.n_cross_language} cross-language`} />
              </div>
              {hasRun(m.pairs.confusion) && (
                <div style={{ ...card, marginTop: 12 }}>
                  <div style={{ ...cap, marginBottom: 10 }}>Confusion matrix · {total(m.pairs.confusion)} pairs</div>
                  <Matrix c={m.pairs.confusion} positive="not supported" negative="supported" />
                </div>
              )}
            </Section>

            <Section title="Article verdict: propaganda pattern or not?" sub={`${m.articles.n_network} articles from known propaganda networks and ${m.articles.n_wire} wire-service articles. The number to look at is the false-positive rate on wire articles: how often we wrongly accuse honest reporting.`}>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <Metric strong name="False-positive rate (wire)" value={pct(m.articles.fpr_wire, m.articles.confusion)} note={`${m.articles.n_wire} wire articles`} />
                <Metric name="True-positive rate (network)" value={pct(m.articles.tpr_network, m.articles.confusion)} note={`${m.articles.n_network} network articles`} />
              </div>
              {hasRun(m.articles.confusion) && (
                <div style={{ ...card, marginTop: 12 }}>
                  <div style={{ ...cap, marginBottom: 10 }}>Confusion matrix · {total(m.articles.confusion)} articles</div>
                  <Matrix c={m.articles.confusion} positive="propaganda pattern" negative="not flagged" />
                </div>
              )}
            </Section>

            <Section title="Rules and thresholds" sub="A propaganda pattern needs two different signals (evidence, infrastructure, citation circle, laundering). One bad claim never produces it, and claims we cannot check never count against an article.">
              <div style={card}>
                {Object.entries(m.thresholds).map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '7px 0', borderTop: `1px solid ${TH.line}`, fontSize: 14 }}>
                    <span style={{ textTransform: 'capitalize', minWidth: 0 }}>{label(k)}</span>
                    <span style={{ fontFamily: MONO, fontWeight: 700, textAlign: 'right' }}>{v}</span>
                  </div>
                ))}
                <div style={{ marginTop: 12, fontSize: 13, color: TH.mut, lineHeight: 1.6 }}>
                  <div>Set on: <b style={{ color: TH.ink }}>{m.tuned_on}</b></div>
                  <div>Measured on: <b style={{ color: TH.ink }}>{m.measured_on}</b></div>
                  <div>Verify model: <b style={{ color: TH.ink }}>{m.verify_model}</b> · prompt <b style={{ color: TH.ink }}>{m.prompt_version}</b> (frozen once the evaluation started)</div>
                </div>
              </div>
            </Section>

            <Section title="Source labels: context, not a verdict" sub="Each source card shows what kind of page it is and what dated public lists say about its domain. These labels help the reader judge a source; they never change a claim verdict or the article verdict.">
              <div style={{ ...card, fontSize: 14, lineHeight: 1.55 }}>
                {[
                  ['Source type', 'Fixed rules on the URL and the page markup, first match wins: social media, academic, press release, official (government hosts and suffixes such as .gov), reference, news, blog. No rule matches: unknown, and no label is shown.'],
                  ['Wikipedia rating', 'The status the Wikipedia community gives the outlet on its Perennial Sources list (reliable, mixed, unreliable, deprecated or spam-blacklisted), from a dated snapshot. Most sites are not listed; that is neutral.'],
                  ['Popularity', 'Rank on the Tranco top-sites research list, from a dated snapshot. Shown as popularity, not reliability: widely read state media rank high too.'],
                  ['Re-registered', 'The Wayback Machine has pages from the domain at least a year before its current registration date. The domain existed before and was registered again, a known way to borrow an old name. A failed archive lookup shows nothing.'],
                ].map(([k, v]) => (
                  <div key={k} style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '150px 1fr', gap: compact ? 3 : 12, padding: '8px 0', borderTop: `1px solid ${TH.line}` }}>
                    <b>{k}</b>
                    <span style={{ color: TH.mut }}>{v}</span>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="Where it goes wrong" sub="Every error case from the run, not a selection.">
              {m.error_cases.length === 0 ? (
                <div style={{ ...card, color: TH.mut }}>No error cases recorded.</div>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  {m.error_cases.map((e, i) => (
                    <div key={i} style={{ ...card, padding: '12px 16px', display: 'grid', gridTemplateColumns: compact ? '1fr' : '90px 1fr', gap: compact ? 8 : 12, alignItems: 'start' }}>
                      <span style={{ fontFamily: MONO, fontWeight: 700, fontSize: 12, padding: '2px 9px', borderRadius: 99, border: `2px solid ${e.kind === 'fp' ? CL.o : CL.r}`, justifySelf: 'start' }}>{e.kind === 'fp' ? 'false pos.' : 'false neg.'}</span>
                      <div style={{ fontSize: 14, lineHeight: 1.5 }}>
                        <div style={{ fontFamily: MONO, fontSize: 12.5, overflowWrap: 'anywhere' }}>{e.url_or_pair_id}</div>
                        <div>Expected <b>{e.expected}</b>, got <b>{e.got}</b>.</div>
                        <div style={{ color: TH.mut }}>{e.note}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Section>

            <p style={{ marginTop: 36, fontSize: 12.5, color: TH.mut }}>Generated {m.generated_at.slice(0, 16).replace('T', ' ')} UTC. Evaluation cases are kept apart from the demo cases.</p>
          </>
        )}
      </div>
    </div>
  );
}
