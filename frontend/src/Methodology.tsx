import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { api } from './api';
import { Wordmark } from './Brand';
import { hasRun, label, pct, total } from './evalstats';
import { useCompact } from './responsive';
import { ARTICLE_V, CL, CLAIM_V, MONO, TH } from './theme';
import type { ArticleVerdict, ClaimVerdict, Confusion, Methodology as M } from './types';

// The public methodology page (PLAN F5, never cut). Texts restate what the backend does (pipeline/*.py, verdict.py,
// DECISIONS.md); every threshold shown comes from GET /api/methodology so the page cannot drift from verdict.py.

const card: CSSProperties = { background: TH.card, border: `1px solid ${TH.line}`, borderRadius: TH.rad, padding: '18px 20px', boxShadow: TH.sh };
const cap: CSSProperties = { fontSize: 11, letterSpacing: '.07em', textTransform: 'uppercase', color: TH.mut, fontWeight: 600 };
const body: CSSProperties = { fontSize: 14, lineHeight: 1.55 };

const SECTIONS: [string, string][] = [
  ['how', 'How a check works'], ['verdicts', 'Claim verdicts'], ['rule', 'Article verdict'], ['guards', 'False-positive guards'],
  ['measured', 'Measured'], ['labels', 'Source labels'], ['limits', 'Known limits'], ['audit', 'Audit trail'],
];

function Section({ id, title, sub, children }: { id: string; title: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <section id={`m-${id}`} style={{ marginTop: 44, scrollMarginTop: 16 }}>
      <h2 style={{ fontSize: 24, letterSpacing: '-.02em', margin: 0 }}>{title}</h2>
      {sub && <p style={{ color: TH.mut, fontSize: 14.5, lineHeight: 1.55, margin: '6px 0 0', maxWidth: 700 }}>{sub}</p>}
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

function ClaimPill({ v }: { v: ClaimVerdict }) {
  const x = CLAIM_V[v];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 10px', borderRadius: 99, border: `2px solid ${x.c}`, fontWeight: 600, fontSize: 13, whiteSpace: 'nowrap', background: TH.card }}>
      <span style={{ color: x.c, fontFamily: MONO, fontWeight: 700 }}>{x.sym}</span>{x.label}
    </span>
  );
}

function ArticlePill({ v }: { v: ArticleVerdict }) {
  const x = ARTICLE_V[v];
  return <span style={{ display: 'inline-block', padding: '3px 11px', borderRadius: 99, background: x.c, color: '#fff', fontWeight: 700, fontSize: 13, whiteSpace: 'nowrap' }}>{x.label}</span>;
}

function Tag({ children, tone }: { children: ReactNode; tone: 'yes' | 'no' | 'mut' }) {
  const c = tone === 'yes' ? CL.r : tone === 'no' ? CL.g : TH.mut;
  return <span style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 700, color: c, border: `1.5px solid ${c}`, borderRadius: 6, padding: '1px 7px', whiteSpace: 'nowrap' }}>{children}</span>;
}

// ---- content ---------------------------------------------------------------------------------------------------

interface Step { name: string; what: ReactNode; guard: ReactNode }

function steps(fuzzy: string, youngDays: string): Step[] {
  return [
    {
      name: 'Read the article',
      what: 'Context.dev fetches the page as text with its links kept (trafilatura as fallback). A main-text filter keeps only the sentences of the article body.',
      guard: 'Teasers, “Top news” boxes and sidebars cannot become claims. In testing, one network article had 4 of its 6 claims taken from teasers of other articles before this filter.',
    },
    {
      name: 'Pick up to 6 central claims',
      what: 'Code splits the text into numbered sentences. A fast model is asked 5 times, independently, which sentences are checkable factual claims; a sentence needs a strict majority. Sentences that name a source are preferred, but 2 slots stay reserved for unsourced ones.',
      guard: 'Asking a model once for “the most central claims” is not repeatable, even at temperature 0. With the vote, the article label was identical in 16 of 16 repeated runs (before: 3 different labels in 3 runs).',
    },
    {
      name: 'Find what each claim cites',
      what: 'Only links and attributions in the claim’s own sentence or paragraph count as its sources. Same-site links are self-citations (we follow them one step to what that page cites). Telegram channels are read through their public preview; X and YouTube are social media and not checked. A named source without a link (“she told Fox Business in May”) is searched for: a search plan, Exa search and the Exa agent look for the original.',
      guard: 'A page we found ourselves, or a link attached only by paragraph, can confirm a claim but never count against the article: a wrong guess must never become evidence.',
    },
    {
      name: 'Check the claim against the source',
      what: 'A strong model reads the source and must first copy the passage the verdict rests on, word for word (original language plus English translation), then give the verdict.',
      guard: <>Code looks for that passage in the fetched page (fuzzy match, threshold {fuzzy}/100). Not found means <b>not checkable</b>, whatever the model said: invented evidence cannot carry a verdict in either direction.</>,
    },
    {
      name: 'Handle claims without a source',
      what: 'One fact-check run of the Exa agent looks for pages that confirm or refute the claim; every page it returns goes through the same quote-first check. A second question decides where the claim starts: the speaker’s own statement or first-hand account (“root”, neutral), or information relayed from somewhere the article does not name (“experts warn”, “studies show”: unsourced).',
      guard: 'Refuting needs a verbatim, located quote. Confirming and refuting pages together mean “sources disagree” (neutral). A doubtful origin is treated as root, so uncertainty never penalises the article.',
    },
    {
      name: 'Fingerprint the infrastructure',
      what: <>From the raw HTML of the article and every cited page: Google Analytics, Tag Manager, AdSense and Yandex.Metrica IDs (an exact shared ID puts domains in one operator cluster). Domain age from RDAP (a cited domain younger than {youngDays} days). The VIGINUM “Portal Kombat” list of documented network domains. Copies of the article on other domains (Exa similarity search, kept only when they repeat its sentences). A page that names a Telegram channel as its source.</>,
      guard: 'Exact matches only, never “looks similar”. Unknown age is never young. Social platforms are never fingerprinted. Pages found by our own search never feed this step: a shared ID between two search results would be a coincidence.',
    },
    {
      name: 'Follow the chain',
      what: 'For each source we take the passage the claim rests on and follow its links, up to 3 hops deep and 14 pages per article. A citation circle closes when a link leads back to the article’s domain or to a domain in its operator cluster. Deeper pages get a quote-first check that is shown in the graph.',
      guard: 'Homepages and site navigation are ignored. Checks below the first hop are display only: they never change a claim verdict or the article verdict.',
    },
    {
      name: 'Decide the article verdict',
      what: 'Claim verdicts and signals go into one fixed rule (below). No model is involved in this step.',
      guard: 'One bad claim can never produce a propaganda pattern; claims we could not check never count against the article.',
    },
  ];
}

const CLAIM_ROWS: [ClaimVerdict, string, boolean][] = [
  ['supported', 'The source contains a passage that makes the claim, and code located that exact passage in the fetched page.', false],
  ['distorted', 'The source says something related, but the article changes it: a number, the scope, the certainty, who did what.', true],
  ['contradicted', 'The source states something incompatible with the claim, in a passage code located in the page.', true],
  ['not_mentioned', 'The source was read, but it does not contain the claim.', true],
  ['unsourced', 'The article gives no source and the information comes from somewhere it does not name (“experts say”).', true],
  ['not_checkable', 'Unreachable, paywalled, social media, a link to the article’s own site, a quote that could not be located, or a root statement (an interview, a first-hand account). Neutral.', false],
];

const GUARDS: [string, string][] = [
  ['A missing detail is not a distortion', 'An early prompt marked articles that left out details as “distorted”. The verify prompt now says an omission is not distortion, and “supported” needs the quoted passage itself to make the assertion.'],
  ['A guessed source can only confirm', 'Search found a briefing from before a trip for an article about the trip’s results, and it “distorted” the claim. A page we found ourselves that disagrees is now neutral.'],
  ['A paragraph link can only confirm', 'A link at the end of a paragraph belonged to its last sentence; two earlier sentences became “not mentioned”. Links attached by paragraph, not sentence, can no longer count against a claim.'],
  ['Navigation links are not citations', 'Topic-tag links (/world/gaza) turned 4 of 6 claims of a Guardian article into self-citations. Same-site section and tag pages are now ignored; links to the site’s own articles stay self-citations.'],
  ['Mirrors are not independent', '15 % of the pages that “confirmed” a quote were syndicated copies of the article itself. A page that repeats 40 % or more of the article’s sentences no longer counts as independent reporting.'],
  ['A fan account is not the speaker', 'A fan account counted as a politician’s own post. User-generated platforms are never treated as the speaker’s own site.'],
  ['The host’s question is not the answer', 'For “X said Y” the quoted passage must show X saying it. Evidence taken from the interviewer’s question is rejected.'],
  ['Echo chambers do not confirm', 'If only other outlets report a quote and the original cannot be found, the claim stays not checkable. Outlets in one network repeat each other.'],
  ['The reader’s question cannot steer', 'A question typed next to the URL only decides which extra sentence is checked. The verify model never sees it, and the article verdict is the same with or without it (tested).'],
];

function limits(): ReactNode[] {
  return [
    <><b>We check the cited sources, not the truth.</b> An article that faithfully quotes a false source gets “supported” for that claim. The laundering signal catches the most common case (republished Telegram posts), not every one.</>,
    <><b>Spoken statements are hard to find.</b> Press-conference quotes and ministries missing from the search index usually stay “not checkable”, which is neutral but tells the reader little.</>,
    <><b>Thresholds are start values.</b> They were chosen on principle before any evaluation and have not been tuned on labelled data.</>,
    <><b>The network list makes listed domains easy.</b> Any article from a VIGINUM-listed host fires the infrastructure signal. Recall on network articles is only meaningful when measured with and without the list.</>,
    <><b>Copies can over-fire.</b> The domain age of a site that copies the article counts toward the infrastructure signal, so a young scraper mirroring a wire story can fire it for the wire story. One signal alone still never produces a propaganda pattern.</>,
    <><b>Refutations are not yet measured.</b> The fact-check agent can mark a claim “contradicted”. How often it does so wrongly on true claims has not been measured yet; it always needs a located quote.</>,
    <><b>Claim selection is a model’s choice.</b> The vote makes it repeatable, not perfect. A sentence that continues the previous one (“It did not give any reason”) can flip between not checkable and unsourced.</>,
    <><b>The web changes.</b> A finished analysis replays exactly from the cache, but a fresh run days later can find different pages and reach a different label.</>,
  ];
}

const THRESHOLD_NAMES: Record<string, string> = {
  min_bad_claims: 'Bad claims needed for the evidence signal',
  young_domain_days: 'Cited domain counts as young below (days)',
  insufficient_ratio: 'Share of not-checkable claims above which: insufficient evidence',
  min_signals_for_pattern: 'Different signals needed for a propaganda pattern',
  min_channel_claims: 'Channel-only claims needed for the laundering signal',
  channel_majority: '… and their share of the linked claims must exceed',
  max_search_only_share: 'Share of claims confirmed only by our search above which: not well sourced',
  fuzzy: 'Quote match threshold (0 to 100)',
};

// ---- page ------------------------------------------------------------------------------------------------------

export function Methodology() {
  const [m, setM] = useState<M | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const compact = useCompact();
  useEffect(() => {
    window.scrollTo(0, 0);
    api.methodology().then(setM, (e) => setErr(String(e.message || e)));
  }, []);

  const run = m ? hasRun(m.pairs.confusion) || hasRun(m.articles.confusion) : false;
  const th = m?.thresholds ?? {};
  const num = (k: string, d: number) => (typeof th[k] === 'number' ? th[k] : d);
  const share = (k: string, d: number) => `${Math.round(num(k, d) * 100)} %`;
  const fuzzy = String(num('fuzzy', 90));
  const go = (id: string) => document.getElementById(`m-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const ruleRows: [string, ArticleVerdict][] = [
    ['At least 2 claim-independent signals fired (infrastructure, circle, laundering). These are facts about the sources, so they decide even when claims could not be checked.', 'propaganda_pattern'],
    [`No central claims, or more than ${share('insufficient_ratio', 0.5)} of them not checkable.`, 'insufficient_evidence'],
    [`At least ${num('min_signals_for_pattern', 2)} different signals fired, the evidence signal included.`, 'propaganda_pattern'],
    ['Only the evidence signal fired.', 'weakly_sourced'],
    [`More than ${share('max_search_only_share', 0.5)} of the checkable claims are confirmed only by pages our search found: the facts hold, but the article cites nothing for them.`, 'weakly_sourced'],
    ['Otherwise. At most one signal fired and the checkable claims hold up.', 'well_sourced'],
  ];

  const signals: [string, string, string][] = [
    ['Evidence', 'depends on claims', `At least ${num('min_bad_claims', 2)} central claims are distorted, contradicted, not mentioned in their source, or unsourced.`],
    ['Infrastructure', 'independent of claims', `The article or a cited page shares an analytics or AdSense ID with another cited domain, a cited domain is younger than ${num('young_domain_days', 90)} days, or a domain is on a published list of a documented influence network.`],
    ['Citation circle', 'independent of claims', 'Following a source’s own citations leads back to the article’s domain or to a domain run by the same operator.'],
    ['Laundering', 'independent of claims', `The page names a Telegram channel as its source, or at least ${num('min_channel_claims', 2)} claims, and more than ${share('channel_majority', 0.5)} of the claims that cite a link, rest only on Telegram or social media posts.`],
  ];

  const stepList = steps(fuzzy, String(num('young_domain_days', 90)));

  return (
    <div style={{ minHeight: '100dvh', background: TH.bg, color: TH.ink, fontFamily: TH.font, backgroundImage: `radial-gradient(${TH.dot} ${TH.dr}px, transparent ${TH.dr}px)`, backgroundSize: `${TH.ds}px ${TH.ds}px` }}>
      <div style={{ maxWidth: 920, margin: '0 auto', padding: compact ? 'max(18px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) 60px max(16px, env(safe-area-inset-left))' : '28px 20px 80px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <a href="#/" title="Back to start" style={{ fontWeight: 700, fontSize: 17, letterSpacing: '-.01em', textDecoration: 'none', color: TH.ink }}><Wordmark /></a>
          <span style={{ color: TH.mut }}>/</span>
          <span style={{ fontWeight: 600, fontSize: 15 }}>Methodology</span>
        </div>

        <h1 style={{ fontSize: 'clamp(30px, 5vw, 46px)', lineHeight: 1.08, letterSpacing: '-.03em', margin: compact ? '28px 0 12px' : '48px 0 12px', maxWidth: 760 }}>How Source Check decides, and where it can be wrong.</h1>
        <p style={{ fontSize: compact ? 15.5 : 17, lineHeight: 1.5, color: TH.mut, margin: 0, maxWidth: 700 }}>
          Source Check does not decide whether a claim is true. It checks whether the sources an article cites actually say what the article claims, follows those sources further, and looks at the infrastructure behind them. Language models read and quote; code decides.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(3, 1fr)', gap: 12, marginTop: 26 }}>
          {[
            ['Quote first, verdict second', 'Every verdict needs a word-for-word passage from the source, and code checks that the passage is really there.'],
            ['Unknown is neutral', 'Dead links, paywalls, social media posts and self-citations never count against an article.'],
            ['Two independent signals', 'A propaganda pattern needs two different signals. One bad claim, or one odd domain, is never enough.'],
          ].map(([t, d]) => (
            <div key={t} style={{ ...card, padding: '16px 18px' }}>
              <div style={{ fontWeight: 700, fontSize: 15.5 }}>{t}</div>
              <div style={{ fontSize: 13.5, color: TH.mut, marginTop: 6, lineHeight: 1.5 }}>{d}</div>
            </div>
          ))}
        </div>

        <nav aria-label="Sections" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 22 }}>
          {SECTIONS.map(([id, t]) => (
            <button key={id} onClick={() => go(id)} style={{ cursor: 'pointer', font: 'inherit', fontSize: 13, fontWeight: 600, padding: compact ? '9px 13px' : '6px 12px', borderRadius: 99, border: `1px solid ${TH.line}`, background: TH.card, color: TH.ink }}>{t}</button>
          ))}
        </nav>

        {err && <div style={{ ...card, marginTop: 28, borderColor: CL.r, color: CL.r, fontWeight: 600 }}>Could not load the live thresholds and metrics ({err}). The values below are the defaults.</div>}

        {/* ---- 1. pipeline ---- */}
        <Section id="how" title="How a check works" sub="Eight steps from a URL to a verdict. Each step lists what it does and what stops it from producing a false alarm.">
          {!compact && <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 18 }}>
            {stepList.map((s, i) => (
              <span key={s.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 12.5, fontWeight: 600, padding: '4px 10px', borderRadius: 8, background: TH.card, border: `1px solid ${TH.line}` }}>
                  <span style={{ fontFamily: MONO, color: TH.mut, marginRight: 5 }}>{i + 1}</span>{s.name}
                </span>
                {i < stepList.length - 1 && <span aria-hidden style={{ color: TH.mut }}>→</span>}
              </span>
            ))}
          </div>}
          <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {stepList.map((s, i) => (
              <li key={s.name} style={{ display: 'grid', gridTemplateColumns: '34px 1fr', gap: compact ? 10 : 14 }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                  <div style={{ width: 30, height: 30, borderRadius: 99, background: TH.ink, color: '#fff', display: 'grid', placeItems: 'center', fontFamily: MONO, fontWeight: 700, fontSize: 13, flex: 'none' }}>{i + 1}</div>
                  {i < stepList.length - 1 && <div aria-hidden style={{ width: 2, flex: 1, background: TH.line, margin: '4px 0' }} />}
                </div>
                <div style={{ ...card, padding: '14px 18px', marginBottom: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>{s.name}</div>
                  <div style={{ ...body, marginTop: 6 }}>{s.what}</div>
                  <div style={{ ...body, marginTop: 10, paddingLeft: 12, borderLeft: `3px solid ${CL.g}`, color: TH.mut }}>
                    <b style={{ color: TH.ink }}>Guard. </b>{s.guard}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </Section>

        {/* ---- 2. claim verdicts ---- */}
        <Section id="verdicts" title="What a claim verdict means" sub="Each central claim gets one of six verdicts, in the same colours as the graph. Only four of them can count against the article.">
          <div style={{ ...card, padding: compact ? '6px 14px' : '6px 20px' }}>
            {CLAIM_ROWS.map(([v, d, against], i) => (
              <div key={v} style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '160px 1fr auto', gap: compact ? 6 : 14, alignItems: 'center', padding: '12px 0', borderTop: i ? `1px solid ${TH.line}` : 'none' }}>
                <div><ClaimPill v={v} /></div>
                <div style={{ ...body, color: TH.mut }}>{d}</div>
                <div style={{ justifySelf: compact ? 'start' : 'end' }}>
                  {against ? <Tag tone="yes">counts against</Tag> : <Tag tone="mut">never counts against</Tag>}
                </div>
              </div>
            ))}
          </div>
          <p style={{ ...body, color: TH.mut, margin: '12px 0 0', maxWidth: 700 }}>
            If our search finds a page that supports a claim the article gave no source for, the claim becomes supported, and the graph shows that the article itself cited nothing for it.
          </p>
        </Section>

        {/* ---- 3. article rule ---- */}
        <Section id="rule" title="From claims to an article verdict" sub="Four signals, one fixed rule, no language model. The values come live from the running backend, so this page shows exactly what the code uses.">
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, 1fr)', gap: 12 }}>
            {signals.map(([name, kind, cond]) => (
              <div key={name} style={{ ...card, padding: '14px 18px' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 700, fontSize: 15.5 }}>{name} signal</span>
                  <span style={cap}>{kind}</span>
                </div>
                <div style={{ ...body, color: TH.mut, marginTop: 6 }}>{cond}</div>
              </div>
            ))}
          </div>

          <div style={{ ...card, marginTop: 14 }}>
            <div style={{ ...cap, marginBottom: 6 }}>The rule, checked top to bottom; the first match wins</div>
            {ruleRows.map(([cond, v], i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: compact ? '26px 1fr' : '26px 1fr auto', gap: compact ? '6px 10px' : 14, alignItems: 'center', padding: '11px 0', borderTop: i ? `1px solid ${TH.line}` : 'none' }}>
                <span style={{ fontFamily: MONO, fontWeight: 700, color: TH.mut }}>{i + 1}</span>
                <span style={body}>{cond}</span>
                <span style={{ gridColumn: compact ? '2' : undefined }}><span aria-hidden style={{ color: TH.mut, marginRight: 8 }}>→</span><ArticlePill v={v} /></span>
              </div>
            ))}
          </div>

          <details style={{ ...card, marginTop: 14 }}>
            <summary style={{ cursor: 'pointer', fontWeight: 600, fontSize: 14 }}>All thresholds and how they were set</summary>
            <div style={{ marginTop: 10 }}>
              {Object.entries(th).map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '7px 0', borderTop: `1px solid ${TH.line}`, fontSize: 14 }}>
                  <span style={{ minWidth: 0 }}>{THRESHOLD_NAMES[k] ?? label(k)}</span>
                  <span style={{ fontFamily: MONO, fontWeight: 700, textAlign: 'right' }}>{v}</span>
                </div>
              ))}
              <div style={{ marginTop: 12, fontSize: 13, color: TH.mut, lineHeight: 1.6 }}>
                {m?.tuned_on ? (
                  <>
                    <div>Set on: <b style={{ color: TH.ink }}>{m.tuned_on}</b></div>
                    <div>Measured on: <b style={{ color: TH.ink }}>{m.measured_on}</b></div>
                  </>
                ) : (
                  <div>These are start values chosen on principle before any evaluation. They have not been tuned, and never on the demo articles.</div>
                )}
              </div>
            </div>
          </details>
        </Section>

        {/* ---- 4. guards ---- */}
        <Section id="guards" title="Guards against false positives" sub="Each rule below exists because we saw the mistake happen on a real article while building. A false accusation is worse than a missed one.">
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
            {GUARDS.map(([t, d]) => (
              <div key={t} style={{ ...card, padding: '14px 18px' }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{t}</div>
                <div style={{ fontSize: 13.5, color: TH.mut, marginTop: 6, lineHeight: 1.5 }}>{d}</div>
              </div>
            ))}
          </div>
        </Section>

        {/* ---- 5. measured ---- */}
        <Section id="measured" title="Measured" sub="Accuracy comes from a labelled evaluation set that is kept apart from the three demo articles. Repeatability was measured while building.">
          {!m && !err && <div style={{ color: TH.mut }}>Loading …</div>}
          {m && !run && (
            <div style={{ ...card, borderColor: CL.y, ...body }}>
              <b>The evaluation has not run yet.</b> Precision, recall and the false-positive rate appear here as soon as <span style={{ fontFamily: MONO, fontSize: 13 }}>eval/results/metrics.json</span> exists. We show an empty state instead of zeros so nobody mistakes missing data for a result.
            </div>
          )}

          {m && (
            <>
              <h3 style={{ fontSize: 17, margin: '22px 0 4px' }}>Claim check: does the source support the claim?</h3>
              <p style={{ ...body, color: TH.mut, margin: '0 0 12px', maxWidth: 700 }}>{m.pairs.n} labelled claim-source pairs, {m.pairs.n_cross_language} of them across languages. The positive class is “not supported” (distorted, contradicted, not mentioned), because that is what we must catch.</p>
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

              <h3 style={{ fontSize: 17, margin: '28px 0 4px' }}>Article verdict: propaganda pattern or not?</h3>
              <p style={{ ...body, color: TH.mut, margin: '0 0 12px', maxWidth: 700 }}>{m.articles.n_network} articles from known propaganda networks and {m.articles.n_wire} wire-service articles. The number to look at is the false-positive rate on wire articles: how often we wrongly accuse honest reporting.</p>
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

              <h3 style={{ fontSize: 17, margin: '28px 0 4px' }}>Where it goes wrong</h3>
              <p style={{ ...body, color: TH.mut, margin: '0 0 12px' }}>Every error case from the run, not a selection.</p>
              {!run ? (
                <div style={{ ...card, color: TH.mut, ...body }}>Error cases appear here after the evaluation run.</div>
              ) : m.error_cases.length === 0 ? (
                <div style={{ ...card, color: TH.mut, ...body }}>No error cases recorded.</div>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  {m.error_cases.map((e, i) => (
                    <div key={i} style={{ ...card, padding: '12px 16px', display: 'grid', gridTemplateColumns: compact ? '1fr' : '90px 1fr', gap: compact ? 8 : 12, alignItems: 'start' }}>
                      <span style={{ fontFamily: MONO, fontWeight: 700, fontSize: 12, padding: '2px 9px', borderRadius: 99, border: `2px solid ${e.kind === 'fp' ? CL.o : CL.r}`, justifySelf: 'start' }}>{e.kind === 'fp' ? 'false pos.' : 'false neg.'}</span>
                      <div style={body}>
                        <div style={{ fontFamily: MONO, fontSize: 12.5, overflowWrap: 'anywhere' }}>{e.url_or_pair_id}</div>
                        <div>Expected <b>{e.expected}</b>, got <b>{e.got}</b>.</div>
                        <div style={{ color: TH.mut }}>{e.note}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          <h3 style={{ fontSize: 17, margin: '28px 0 4px' }}>Repeatability</h3>
          <p style={{ ...body, color: TH.mut, margin: '0 0 12px', maxWidth: 700 }}>Measured while building, on articles outside the demo set (claim overlap: DW, Al Jazeera, Guardian, TASS). These numbers show that the same article gets the same answer, not that the answer is right.</p>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Metric name="Same claims picked" value="0.66 → 1.00" note="Mean overlap of the claim sets over 5 repeats on identical text, single ranked call vs. 5-vote majority." />
            <Metric name="Same article label" value="16 / 16" note="4 articles × 4 fresh runs. Before the vote: 3 different labels in 3 runs of one article." />
            <Metric name="Replay" value="identical" note="A finished analysis replays byte for byte from the step cache." />
          </div>
        </Section>

        {/* ---- 6. source labels ---- */}
        <Section id="labels" title="Source labels: context, not a verdict" sub="Each source card shows what kind of page it is and what dated public lists say about its domain. These labels help the reader judge a source; they never change a claim verdict or the article verdict.">
          <div style={{ ...card, ...body }}>
            {[
              ['Source type', 'Fixed rules on the URL and the page markup, first match wins: social media, academic, press release, official (government hosts and suffixes such as .gov), reference, news, blog. No rule matches: unknown, and no label is shown. The type says what kind of document it is, not whether it is reliable: kremlin.ru is official.'],
              ['Wikipedia rating', 'The status the Wikipedia community gives the outlet on its Perennial Sources list (reliable, mixed, unreliable, deprecated or spam-blacklisted), from a dated snapshot. Most sites are not listed; that is neutral.'],
              ['Popularity', 'Rank on the Tranco top-sites research list, from a dated snapshot. Shown as popularity, not reliability: widely read state media rank high too.'],
              ['Re-registered', 'The Wayback Machine has pages from the domain at least a year before its current registration date. The domain existed before and was registered again, a known way to borrow an old name. A failed archive lookup shows nothing.'],
            ].map(([k, v], i) => (
              <div key={k} style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '150px 1fr', gap: compact ? 3 : 12, padding: '8px 0', borderTop: i ? `1px solid ${TH.line}` : 'none' }}>
                <b>{k}</b>
                <span style={{ color: TH.mut }}>{v}</span>
              </div>
            ))}
          </div>
        </Section>

        {/* ---- 7. limits ---- */}
        <Section id="limits" title="Known limits" sub="What the tool cannot do, or does not do well yet.">
          <div style={{ ...card, padding: compact ? '6px 14px' : '6px 20px' }}>
            {limits().map((x, i) => (
              <div key={i} style={{ ...body, padding: '11px 0', borderTop: i ? `1px solid ${TH.line}` : 'none' }}>{x}</div>
            ))}
          </div>
        </Section>

        {/* ---- 8. audit ---- */}
        <Section id="audit" title="Audit trail" sub="Every verdict can be traced back to the words it rests on.">
          <div style={{ ...card, ...body }}>
            <p style={{ margin: 0 }}>Each claim verdict is stored with the verbatim evidence passage (original and translation), its quote-match score, the model that produced it and the prompt version. Every step’s input and answer is cached by a hash of the input, so a finished analysis can be replayed and inspected exactly.</p>
            <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(3, 1fr)', gap: 12, marginTop: 14 }}>
              {[
                ['Verify model', m?.verify_model || 'not reported'],
                ['Prompt version', m?.prompt_version || 'not reported'],
                ['Quote match threshold', `${fuzzy} / 100`],
              ].map(([k, v]) => (
                <div key={k}>
                  <div style={cap}>{k}</div>
                  <div style={{ fontFamily: MONO, fontWeight: 700, marginTop: 4, overflowWrap: 'anywhere' }}>{v}</div>
                </div>
              ))}
            </div>
            <p style={{ margin: '14px 0 0', color: TH.mut }}>Language models run through the Hostinger AI Router. The three demo articles are precomputed so the demo works with every external service down; evaluation articles are kept apart from them, and no threshold or prompt was tuned on them.</p>
          </div>
        </Section>

        {m && <p style={{ marginTop: 36, fontSize: 12.5, color: TH.mut }}>Thresholds and metrics loaded {m.generated_at.slice(0, 16).replace('T', ' ')} UTC.</p>}
      </div>
    </div>
  );
}
