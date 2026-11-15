import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { api } from './api';
import { Wordmark } from './Brand';
import { ciText, hasRun, label, pct, total } from './evalstats';
import { useCompact } from './responsive';
import { ARTICLE_V, CL, CLAIM_V, GLY, MONO, TH } from './theme';
import type { ArticleVerdict, ClaimVerdict, Confusion, Methodology as M } from './types';

// The public methodology page (PLAN F5, never cut). Built to be read at a glance: what the tool does (capabilities),
// how (one diagram), what comes out (verdicts), and how well (measured). Details stay one click away in <details>.
// Texts restate what the backend does (pipeline/*.py, verdict.py, DECISIONS.md); thresholds come from GET /api/methodology.

const card: CSSProperties = { background: TH.card, border: `1px solid ${TH.line}`, borderRadius: TH.rad, padding: '18px 20px', boxShadow: TH.sh };
const cap: CSSProperties = { fontSize: 11, letterSpacing: '.07em', textTransform: 'uppercase', color: TH.mut, fontWeight: 600 };
const body: CSSProperties = { fontSize: 14, lineHeight: 1.55 };
const BLUE = '#3b5bdb';

const ICON = {
  ...GLY,
  check: 'M5 12.5l4.2 4.2L19 7',
  search: 'M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM15.5 15.5L20 20',
  server: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01',
  cycle: 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4',
  rule: 'M12 4v16M6 20h12M5 8h14M5 8l-2.5 5.5h5zM19 8l-2.5 5.5h5z',
  shield: 'M12 3l7.5 3v6c0 4.6-3.2 7.8-7.5 9-4.3-1.2-7.5-4.4-7.5-9V6z',
};
type IconName = keyof typeof ICON;

function Icon({ name, size = 20, color = 'currentColor' }: { name: IconName; size?: number; color?: string }) {
  return (
    <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none' }}>
      <path d={ICON[name]} />
    </svg>
  );
}

const SECTIONS: [string, string][] = [
  ['how', 'How it works'], ['output', 'What you get'], ['guards', 'Safeguards'], ['measured', 'Results'], ['scope', 'Scope & audit'], ['stack', 'Tech stack'],
];

// Restates backend/pyproject.toml, frontend/package.json and deploy/docker-compose.yml.
const STACK: [string, string][] = [
  ['Frontend', 'React 18, TypeScript and Vite. The source graph is drawn by our own SVG renderer.'],
  ['Backend', 'Python with FastAPI. One background thread per analysis, progress streamed live (SSE).'],
  ['Language models', 'Claude models through the Anthropic API: a strong model verifies, a cheap one picks claims. The Hostinger AI Router is a selectable alternative.'],
  ['Quote matching', 'rapidfuzz finds the model’s quote in the fetched page; trafilatura extracts the article body.'],
  ['Web data', 'Context.dev fetches pages, Exa finds missing sources, RDAP gives domain age, the Wayback Machine the first capture.'],
  ['Storage', 'MariaDB holds results and the audit trail; every step is cached by a hash of its input.'],
  ['Deploy', 'Docker Compose on a Hostinger VPS: MariaDB, the backend and Caddy, which serves the app and handles HTTPS.'],
];

function Section({ id, title, sub, children }: { id: string; title: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <section id={`m-${id}`} style={{ marginTop: 56, scrollMarginTop: 16 }}>
      <h2 style={{ fontSize: 26, letterSpacing: '-.02em', margin: 0 }}>{title}</h2>
      {sub && <p style={{ color: TH.mut, fontSize: 15, lineHeight: 1.55, margin: '6px 0 0', maxWidth: 680 }}>{sub}</p>}
      <div style={{ marginTop: 18 }}>{children}</div>
    </section>
  );
}

function More({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details style={{ ...card, padding: '14px 20px', marginTop: 12 }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600, fontSize: 14 }}>{title}</summary>
      <div style={{ marginTop: 12 }}>{children}</div>
    </details>
  );
}

function Stat({ value, title, note }: { value: string; title: string; note: string }) {
  return (
    <div style={{ flex: '1 1 200px', minWidth: 0 }}>
      <div style={{ fontFamily: MONO, fontWeight: 700, fontSize: 34, letterSpacing: '-.02em' }}>{value}</div>
      <div style={{ fontWeight: 700, fontSize: 14.5, marginTop: 2 }}>{title}</div>
      <div style={{ fontSize: 12.5, color: TH.mut, marginTop: 3, lineHeight: 1.4 }}>{note}</div>
    </div>
  );
}

function Metric({ name, value, note, tone }: { name: string; value: string; note?: string; tone?: string }) {
  return (
    <div style={{ flex: '1 1 150px', minWidth: 0, padding: '12px 14px', borderRadius: 12, background: TH.bg }}>
      <div style={cap}>{name}</div>
      <div style={{ fontFamily: MONO, fontWeight: 700, fontSize: 26, marginTop: 6, color: tone ?? TH.ink }}>{value}</div>
      {note && <div style={{ fontSize: 12, color: TH.mut, marginTop: 4, lineHeight: 1.4 }}>{note}</div>}
    </div>
  );
}

// Rows = truth, columns = our prediction. "Positive" is the class the section names.
function Matrix({ c, positive, negative }: { c: Confusion; positive: string; negative: string }) {
  const cell = (n: number, tag: string, good: boolean): ReactNode => (
    <div style={{ padding: '12px 8px', textAlign: 'center', background: good ? CL.g + '1f' : TH.bg, borderRadius: 10 }}>
      <div style={{ fontFamily: MONO, fontWeight: 700, fontSize: 20 }}>{n}</div>
      <div style={{ fontSize: 11, color: TH.mut, marginTop: 2 }}>{tag}</div>
    </div>
  );
  const head: CSSProperties = { ...cap, textAlign: 'center', alignSelf: 'end' };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(70px, 110px) 1fr 1fr', gap: 6, maxWidth: 420 }}>
      <div />
      <div style={head}>flagged: {positive}</div>
      <div style={head}>flagged: {negative}</div>
      <div style={{ ...cap, alignSelf: 'center' }}>truth: {positive}</div>
      {cell(c.tp, 'correct', true)}
      {cell(c.fn, 'missed', false)}
      <div style={{ ...cap, alignSelf: 'center' }}>truth: {negative}</div>
      {cell(c.fp, 'false alarm', false)}
      {cell(c.tn, 'correct', true)}
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

// ---- diagram ---------------------------------------------------------------------------------------------------

interface Node { icon: IconName; title: string; text: string }

const LANE_A: Node[] = [
  { icon: 'claim', title: 'Up to 6 central claims', text: 'Verbatim sentences, picked by majority over repeated readings' },
  { icon: 'link', title: 'Find what each cites', text: 'Links, named sources, Telegram posts; missing ones searched' },
  { icon: 'check', title: 'Quote-first check', text: 'The model must quote the source; code finds that quote' },
];
const LANE_B: Node[] = [
  { icon: 'server', title: 'Fingerprint the sites', text: 'Shared analytics & AdSense IDs, domain age, known network domains' },
  { icon: 'cycle', title: 'Follow the chain', text: 'Up to 3 hops: does it cite back to its own operator?' },
  { icon: 'telegram', title: 'Spot laundering', text: 'Facts that rest only on Telegram or social posts' },
];

function Box({ n, tone }: { n: Node; tone: string }) {
  return (
    <div style={{ background: TH.card, border: `1px solid ${TH.line}`, borderLeft: `3px solid ${tone}`, borderRadius: 12, padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
      <span style={{ color: tone, marginTop: 1 }}><Icon name={n.icon} size={19} /></span>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 14, lineHeight: 1.3 }}>{n.title}</div>
        <div style={{ fontSize: 12.5, color: TH.mut, marginTop: 2, lineHeight: 1.4 }}>{n.text}</div>
      </div>
    </div>
  );
}

const Down = () => <div aria-hidden style={{ textAlign: 'center', color: TH.mut, fontSize: 14, lineHeight: 1, padding: '3px 0' }}>↓</div>;

function Lane({ title, nodes, tone }: { title: string; nodes: Node[]; tone: string }) {
  return (
    <div style={{ background: tone + '0d', border: `1px solid ${tone}33`, borderRadius: 16, padding: 12, minWidth: 0 }}>
      <div style={{ ...cap, color: tone, margin: '2px 0 10px 2px' }}>{title}</div>
      {nodes.map((n, i) => (
        <div key={n.title}>
          {i > 0 && <Down />}
          <Box n={n} tone={tone} />
        </div>
      ))}
    </div>
  );
}

function Terminal({ icon, title, text, dark }: { icon: IconName; title: string; text: string; dark?: boolean }) {
  return (
    <div style={{ background: dark ? TH.ink : TH.card, color: dark ? '#fff' : TH.ink, border: `1px solid ${dark ? TH.ink : TH.line}`, borderRadius: 14, padding: '11px 18px', display: 'flex', alignItems: 'center', gap: 12, maxWidth: 440, margin: '0 auto' }}>
      <Icon name={icon} size={22} />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 14.5 }}>{title}</div>
        <div style={{ fontSize: 12.5, opacity: 0.72, lineHeight: 1.35, marginTop: 1 }}>{text}</div>
      </div>
    </div>
  );
}

// Connector that splits the input into the two lanes (or joins them again); lane centres sit at 25 % and 75 %.
function Fork({ join }: { join?: boolean }) {
  const d = join ? 'M25 0 V14 M75 0 V14 M25 14 H75 M50 14 V28' : 'M50 0 V14 M25 14 H75 M25 14 V28 M75 14 V28';
  return (
    <svg aria-hidden viewBox="0 0 100 28" preserveAspectRatio="none" style={{ display: 'block', width: '100%', height: 26 }}>
      <path d={d} fill="none" stroke={TH.mut} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Diagram({ compact }: { compact: boolean }) {
  return (
    <div style={{ ...card, padding: compact ? 12 : 22 }}>
      <Terminal icon="article" title="Article URL" text="Fetched as text; only the article body is read" />
      {compact ? <><Down /><div style={{ ...cap, textAlign: 'center', marginBottom: 6 }}>two checks in parallel</div></> : <Fork />}
      <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '1fr 1fr', gap: compact ? 10 : 20 }}>
        <Lane title="1 · Does the source say it?" nodes={LANE_A} tone={CL.g} />
        <Lane title="2 · Who is behind the sources?" nodes={LANE_B} tone={BLUE} />
      </div>
      {compact ? <Down /> : <Fork join />}
      <Terminal dark icon="rule" title="Two-signal rule" text="Fixed code, no AI: one bad claim is never enough for an accusation" />
      <Down />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center' }}>
        {(Object.keys(ARTICLE_V) as ArticleVerdict[]).map((v) => <ArticlePill key={v} v={v} />)}
      </div>
    </div>
  );
}

// ---- content ---------------------------------------------------------------------------------------------------

const CAPABILITIES: [IconName, string, string][] = [
  ['link', 'Traces every citation', 'Links, named sources and Telegram channels, followed up to three hops deep and shown as a graph.'],
  ['check', 'Quote-verified verdicts', 'Each claim verdict rests on a passage that code locates word for word in the source.'],
  ['server', 'Infrastructure fingerprints', 'Shared analytics and AdSense IDs, freshly registered domains and documented network domains.'],
  ['cycle', 'Citation circles', 'Detects sources that lead back to the article’s own site or to sites run by the same operator.'],
  ['telegram', 'Telegram laundering', 'Flags articles whose facts rest only on channel posts, the typical route of laundered content.'],
  ['shield', 'Built against false alarms', 'Unknown stays neutral, and a propaganda pattern needs two independent signals.'],
];

interface Step { name: string; what: ReactNode; guard: ReactNode }

function steps(fuzzy: string, youngDays: string): Step[] {
  return [
    {
      name: 'Read the article',
      what: 'Context.dev fetches the page as text with its links kept (trafilatura as fallback). A main-text filter keeps only the sentences of the article body.',
      guard: 'Teasers, “Top news” boxes and sidebars cannot become claims.',
    },
    {
      name: 'Pick up to 6 central claims',
      what: 'Code splits the text into numbered sentences. A model summarises the article into key points, each citing the sentences that state it, three times independently; a sentence needs a majority. Two slots go to checkable details (figures, dates, attributed statements). A claim is always a verbatim sentence of the article; one that needs its context is rewritten minimally for checking only.',
      guard: 'Repeated readings and a majority make the choice repeatable. Anecdotes and opinions are replaced by the next checkable sentence.',
    },
    {
      name: 'Find what each claim cites',
      what: 'Only links and attributions in the claim’s own sentence or paragraph count as its sources. Same-site links are self-citations. Telegram channels are read through their public preview. A named source without a link (“she told Fox Business in May”) is searched for with Exa.',
      guard: 'A page we found ourselves can confirm a claim but never count against the article: a wrong guess must never become evidence.',
    },
    {
      name: 'Check the claim against the source',
      what: 'A strong model reads the source and must first copy the passage the verdict rests on, word for word (original language plus English translation), then give the verdict.',
      guard: <>Code looks for that passage in the fetched page (fuzzy match, threshold {fuzzy}/100). Not found means <b>not checkable</b>: invented evidence cannot carry a verdict.</>,
    },
    {
      name: 'Handle claims without a source',
      what: 'A fact-check search looks for pages that confirm or refute the claim, with the same quote-first check. A second question decides whether the claim is the speaker’s own statement (neutral) or relayed from somewhere the article does not name (“experts warn”: unsourced).',
      guard: 'Confirming and refuting pages together mean “sources disagree” (neutral). A doubtful origin counts in the article’s favour.',
    },
    {
      name: 'Fingerprint the infrastructure',
      what: <>From the raw HTML of the article and every cited page: Google Analytics, Tag Manager, AdSense and Yandex.Metrica IDs (an exact shared ID puts domains in one operator cluster), domain age from RDAP (younger than {youngDays} days), the VIGINUM “Portal Kombat” list, copies of the article on other domains, and Telegram channels named as the source.</>,
      guard: 'Exact matches only. Unknown age is never young. Social platforms and pages found by our own search are never fingerprinted.',
    },
    {
      name: 'Follow the chain',
      what: 'For each source we follow the links in the passage the claim rests on, up to 3 hops and 14 pages per article. A citation circle closes when a link leads back to the article’s domain or its operator cluster.',
      guard: 'Homepages and navigation are ignored. Deeper checks are shown in the graph and never change a verdict.',
    },
    {
      name: 'Decide the article verdict',
      what: 'Claim verdicts and signals go into one fixed rule. No model is involved in this step.',
      guard: 'One bad claim can never produce a propaganda pattern; claims we could not check never count against the article.',
    },
  ];
}

const CLAIM_ROWS: [ClaimVerdict, string][] = [
  ['supported', 'The source makes the claim, in a passage code located in the page.'],
  ['distorted', 'The source says something related, but a number, the scope or the certainty changed.'],
  ['contradicted', 'The source states the opposite, in a located passage.'],
  ['not_mentioned', 'The source was read and does not contain the claim.'],
  ['unsourced', 'No source given, and the information comes from somewhere unnamed (“experts say”).'],
  ['not_checkable', 'Unreachable, paywalled, social media, self-citation or a first-hand statement. Neutral.'],
];

const ARTICLE_ROWS: [ArticleVerdict, string][] = [
  ['well_sourced', 'The checkable claims hold up in their sources.'],
  ['weakly_sourced', 'Claims fail their sources, or the facts hold but the article cites nothing for them.'],
  ['propaganda_pattern', 'At least two independent signals: bad evidence plus suspicious infrastructure, a citation circle or laundering.'],
  ['insufficient_evidence', 'Too few claims could be checked to say anything. Never an accusation.'],
];

const GUARDS: [string, string][] = [
  ['Quote first, verdict second', 'Every verdict needs a word-for-word passage from the source, and code checks that the passage is really there.'],
  ['Unknown is neutral', 'Dead links, paywalls, social media posts and self-citations never count against an article.'],
  ['Two independent signals', 'One bad claim, or one odd domain, is never enough for a propaganda pattern.'],
  ['Omission is not distortion', 'Leaving out details does not count as distorting; “supported” needs the passage itself to make the claim.'],
  ['Our own search can only confirm', 'Pages we found ourselves, and links attached only by paragraph, can support a claim but never count against it.'],
  ['Mirrors are not independent', 'A page that repeats 40 % or more of the article’s sentences is a copy, not independent confirmation.'],
  ['Echo chambers do not confirm', 'If only other outlets repeat a quote and the original cannot be found, the claim stays not checkable.'],
  ['The speaker must say it', 'For “X said Y” the quote must show X saying it, not an interviewer’s question or a fan account.'],
  ['The reader cannot steer', 'A question typed next to the URL picks one extra sentence to check; the verdict model never sees it.'],
];

const DATASETS: Record<string, [string, string]> = {
  'check-covid': ['Check-COVID', 'Claims journalists wrote, checked by experts against the cited study (ACL 2023).'],
  wice: ['WiCE', 'Wikipedia sentences and the page they cite, labelled by annotators (EMNLP 2023).'],
};

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

  const pairsRun = !!m && hasRun(m.pairs.confusion);
  const artsRun = !!m && hasRun(m.articles.confusion);
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
    [`More than ${share('max_search_only_share', 0.5)} of the checkable claims are confirmed only by pages our search found.`, 'weakly_sourced'],
    ['Otherwise.', 'well_sourced'],
  ];

  const signals: [string, string][] = [
    ['Evidence', `At least ${num('min_bad_claims', 2)} central claims distorted, contradicted, not mentioned or unsourced.`],
    ['Infrastructure', `Shared analytics or AdSense ID, a cited domain younger than ${num('young_domain_days', 90)} days, or a documented network domain.`],
    ['Citation circle', 'A source’s own citations lead back to the article’s domain or its operator.'],
    ['Laundering', `The page names a Telegram channel as its source, or at least ${num('min_channel_claims', 2)} claims rest only on Telegram or social posts.`],
  ];

  const stepList = steps(fuzzy, String(num('young_domain_days', 90)));
  const a = m?.articles;
  const p = m?.pairs;

  return (
    <div style={{ minHeight: '100dvh', background: TH.bg, color: TH.ink, fontFamily: TH.font, backgroundImage: `radial-gradient(${TH.dot} ${TH.dr}px, transparent ${TH.dr}px)`, backgroundSize: `${TH.ds}px ${TH.ds}px` }}>
      <div style={{ maxWidth: 1000, margin: '0 auto', padding: compact ? 'max(18px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) 60px max(16px, env(safe-area-inset-left))' : '28px 20px 80px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <a href="#/" title="Back to start" style={{ fontWeight: 700, fontSize: 17, letterSpacing: '-.01em', textDecoration: 'none', color: TH.ink }}><Wordmark /></a>
          <span style={{ color: TH.mut }}>/</span>
          <span style={{ fontWeight: 600, fontSize: 15 }}>How it works</span>
        </div>

        {/* ---- hero ---- */}
        <h1 style={{ fontSize: 'clamp(30px, 5vw, 48px)', lineHeight: 1.06, letterSpacing: '-.03em', margin: compact ? '28px 0 12px' : '48px 0 14px', maxWidth: 760 }}>Every claim, traced to its source.</h1>
        <p style={{ fontSize: compact ? 15.5 : 17.5, lineHeight: 1.5, color: TH.mut, margin: 0, maxWidth: 700 }}>
          Source Check reads a news article, follows each central claim to the source it cites, checks word for word whether that source really says it, and looks at who runs the sites behind it. Language models read and quote; fixed code decides.
        </p>

        {pairsRun && artsRun && a && p && (
          <div style={{ ...card, marginTop: 26, display: 'flex', flexWrap: 'wrap', gap: compact ? 18 : 28, alignItems: 'flex-start' }}>
            <Stat value={`${a.confusion.fp} / ${a.n_wire}`} title="false alarms on mainstream news" note="BBC, DW, NPR, Guardian, Al Jazeera, Kyiv Independent and more" />
            <Stat value={`${a.confusion.tp} / ${a.n_network}`} title="propaganda-network articles flagged" note="Articles from the documented Pravda network" />
            <Stat value={pct(p.precision, p.confusion)} title="precision on expert-labelled claims" note={`${p.n} claim-source pairs from two public research datasets`} />
            <button onClick={() => go('measured')} style={{ alignSelf: 'center', cursor: 'pointer', font: 'inherit', fontSize: 13, fontWeight: 600, padding: '8px 14px', borderRadius: 99, border: `1px solid ${TH.line}`, background: TH.bg, color: TH.ink }}>How we measured ↓</button>
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(3, 1fr)', gap: 12, marginTop: 14 }}>
          {CAPABILITIES.map(([icon, t, d]) => (
            <div key={t} style={{ ...card, padding: '15px 17px', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <span style={{ width: 34, height: 34, borderRadius: 10, background: TH.bg, display: 'grid', placeItems: 'center', flex: 'none' }}><Icon name={icon} size={18} /></span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{t}</div>
                <div style={{ fontSize: 13, color: TH.mut, marginTop: 4, lineHeight: 1.45 }}>{d}</div>
              </div>
            </div>
          ))}
        </div>

        <nav aria-label="Sections" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 22 }}>
          {SECTIONS.map(([id, t]) => (
            <button key={id} onClick={() => go(id)} style={{ cursor: 'pointer', font: 'inherit', fontSize: 13, fontWeight: 600, padding: compact ? '9px 13px' : '6px 12px', borderRadius: 99, border: `1px solid ${TH.line}`, background: TH.card, color: TH.ink }}>{t}</button>
          ))}
        </nav>

        {err && <div style={{ ...card, marginTop: 28, color: TH.mut, ...body }}>Live thresholds could not be loaded ({err}); the values below are the defaults.</div>}

        {/* ---- 1. diagram ---- */}
        <Section id="how" title="How it works" sub="Two checks run side by side: whether each source supports its claim, and who stands behind the sources. A fixed rule combines both.">
          <Diagram compact={compact} />
          <More title="Step by step, with the safeguard of each step">
            <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {stepList.map((s, i) => (
                <li key={s.name} style={{ display: 'grid', gridTemplateColumns: '28px 1fr', gap: 10, padding: '12px 0', borderTop: i ? `1px solid ${TH.line}` : 'none' }}>
                  <span style={{ width: 24, height: 24, borderRadius: 99, background: TH.ink, color: '#fff', display: 'grid', placeItems: 'center', fontFamily: MONO, fontWeight: 700, fontSize: 12 }}>{i + 1}</span>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 15 }}>{s.name}</div>
                    <div style={{ ...body, marginTop: 4 }}>{s.what}</div>
                    <div style={{ ...body, marginTop: 8, paddingLeft: 12, borderLeft: `3px solid ${CL.g}`, color: TH.mut }}><b style={{ color: TH.ink }}>Safeguard. </b>{s.guard}</div>
                  </div>
                </li>
              ))}
            </ol>
          </More>
        </Section>

        {/* ---- 2. output ---- */}
        <Section id="output" title="What you get" sub="A verdict for every central claim, one verdict for the article, and a graph of every source with the exact passage each verdict rests on.">
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '1.15fr 1fr', gap: 12 }}>
            <div style={card}>
              <div style={{ ...cap, marginBottom: 4 }}>Per claim</div>
              {CLAIM_ROWS.map(([v, d]) => (
                <div key={v} style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '150px 1fr', gap: compact ? 4 : 12, alignItems: 'center', padding: '9px 0', borderTop: `1px solid ${TH.line}` }}>
                  <div><ClaimPill v={v} /></div>
                  <div style={{ fontSize: 13.5, lineHeight: 1.45, color: TH.mut }}>{d}</div>
                </div>
              ))}
            </div>
            <div style={card}>
              <div style={{ ...cap, marginBottom: 4 }}>Per article</div>
              {ARTICLE_ROWS.map(([v, d]) => (
                <div key={v} style={{ padding: '9px 0', borderTop: `1px solid ${TH.line}` }}>
                  <ArticlePill v={v} />
                  <div style={{ fontSize: 13.5, lineHeight: 1.45, color: TH.mut, marginTop: 6 }}>{d}</div>
                </div>
              ))}
            </div>
          </div>
          <More title="The four signals and the exact rule">
            <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, 1fr)', gap: 10 }}>
              {signals.map(([name, cond]) => (
                <div key={name} style={{ padding: '10px 12px', borderRadius: 12, background: TH.bg }}>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{name} signal</div>
                  <div style={{ fontSize: 13, color: TH.mut, marginTop: 3, lineHeight: 1.45 }}>{cond}</div>
                </div>
              ))}
            </div>
            <div style={{ ...cap, margin: '16px 0 4px' }}>Checked top to bottom; the first match wins</div>
            {ruleRows.map(([cond, v], i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: compact ? '22px 1fr' : '22px 1fr auto', gap: compact ? '6px 10px' : 12, alignItems: 'center', padding: '9px 0', borderTop: `1px solid ${TH.line}` }}>
                <span style={{ fontFamily: MONO, fontWeight: 700, color: TH.mut }}>{i + 1}</span>
                <span style={{ fontSize: 13.5, lineHeight: 1.45 }}>{cond}</span>
                <span style={{ gridColumn: compact ? '2' : undefined }}><ArticlePill v={v} /></span>
              </div>
            ))}
            <div style={{ ...cap, margin: '16px 0 4px' }}>Thresholds, live from the running backend</div>
            {Object.entries(th).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '6px 0', borderTop: `1px solid ${TH.line}`, fontSize: 13.5 }}>
                <span style={{ minWidth: 0 }}>{THRESHOLD_NAMES[k] ?? label(k)}</span>
                <span style={{ fontFamily: MONO, fontWeight: 700 }}>{v}</span>
              </div>
            ))}
          </More>
        </Section>

        {/* ---- 3. safeguards ---- */}
        <Section id="guards" title="Safeguards against false alarms" sub="Calling honest reporting propaganda would be the worst mistake, so the design leans towards caution. Each rule below came out of testing on real articles.">
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(3, 1fr)', gap: 12 }}>
            {GUARDS.map(([t, d]) => (
              <div key={t} style={{ ...card, padding: '14px 17px' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontWeight: 700, fontSize: 14.5 }}><Icon name="shield" size={16} color={CL.g} />{t}</div>
                <div style={{ fontSize: 13, color: TH.mut, marginTop: 6, lineHeight: 1.45 }}>{d}</div>
              </div>
            ))}
          </div>
        </Section>

        {/* ---- 4. measured ---- */}
        <Section id="measured" title="Results" sub="Measured on labelled data kept apart from the demo articles. Thresholds were fixed before the run and never tuned on it.">
          {!m && !err && <div style={{ color: TH.mut }}>Loading …</div>}
          {m && !pairsRun && !artsRun && (
            <div style={{ ...card, ...body, color: TH.mut }}>Results appear here once the evaluation has run.</div>
          )}
          {m && (pairsRun || artsRun) && a && p && (
            <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, 1fr)', gap: 12 }}>
              {artsRun && (
                <div style={card}>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>Article verdict</div>
                  <div style={{ fontSize: 13.5, color: TH.mut, margin: '4px 0 12px', lineHeight: 1.45 }}>{a.n_network} articles from the documented Pravda network and {a.n_wire} mainstream news articles, labelled by where they were published.</div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <Metric name="False alarms" value={`${a.confusion.fp} / ${a.n_wire}`} tone={CL.g} note="on mainstream news" />
                    <Metric name="Flagged" value={`${a.confusion.tp} / ${a.n_network}`} note="network articles" />
                  </div>
                  {a.confusion_without_list && (
                    <div style={{ fontSize: 13, color: TH.mut, marginTop: 12, lineHeight: 1.5 }}>
                      Network articles are on the published VIGINUM list. Without that list, the source checks alone still flag <b style={{ color: TH.ink }}>{a.confusion_without_list.tp} of {a.n_network}</b>, again with {a.confusion_without_list.fp} false alarms.
                      {a.n_hard_negatives ? <> The mainstream set includes {a.n_hard_negatives} outlets that often cite Telegram channels; {a.fp_hard_negatives ?? 0} were flagged.</> : null}
                    </div>
                  )}
                </div>
              )}
              {pairsRun && (
                <div style={card}>
                  <div style={{ fontWeight: 700, fontSize: 16 }}>Claim check</div>
                  <div style={{ fontSize: 13.5, color: TH.mut, margin: '4px 0 12px', lineHeight: 1.45 }}>{p.n} claim-source pairs labelled by experts in two public research datasets (Check-COVID, WiCE). Does the source support the claim?</div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <Metric name="Precision" value={pct(p.precision, p.confusion)} note="flagged claims that truly fail" />
                    <Metric name="Recall" value={pct(p.recall, p.confusion)} note="failing claims we catch" />
                  </div>
                  {p.by_dataset && (
                    <div style={{ fontSize: 13, color: TH.mut, marginTop: 12, lineHeight: 1.5 }}>
                      {Object.entries(p.by_dataset).map(([k, d]) => `${DATASETS[k]?.[0] ?? k}: precision ${pct(d.precision, d.confusion)}`).join(' · ')}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <div style={{ ...card, marginTop: 12, display: 'flex', gap: compact ? 14 : 28, flexWrap: 'wrap' }}>
            <div style={{ ...cap, flexBasis: '100%' }}>Repeatability</div>
            <Stat value="16 / 16" title="same article label" note="4 articles × 4 fresh runs, measured while building" />
            <Stat value="0.66 → 1.00" title="same claims picked" note="single call vs. majority of repeated readings" />
            <Stat value="identical" title="replay" note="a finished analysis replays exactly from its cache" />
          </div>
          {m && (pairsRun || artsRun) && a && p && (
            <More title="Full numbers: confusion matrices and 95 % intervals">
              <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, 1fr)', gap: 20 }}>
                {artsRun && (
                  <div>
                    <div style={{ ...cap, marginBottom: 8 }}>Articles · {total(a.confusion)}</div>
                    <Matrix c={a.confusion} positive="propaganda" negative="not flagged" />
                    <div style={{ fontSize: 12.5, color: TH.mut, marginTop: 8, lineHeight: 1.5 }}>
                      False-alarm rate {pct(a.fpr_wire, a.confusion)} ({ciText(a.confusion.fp, a.n_wire)}) · detection rate {pct(a.tpr_network, a.confusion)} ({ciText(a.confusion.tp, a.n_network)})
                      {a.confusion_without_list ? <> · without the list {pct(a.tpr_network_without_list ?? 0, a.confusion)} ({ciText(a.confusion_without_list.tp, a.n_network)})</> : null}
                    </div>
                  </div>
                )}
                {pairsRun && (
                  <div>
                    <div style={{ ...cap, marginBottom: 8 }}>Claim-source pairs · {total(p.confusion)}</div>
                    <Matrix c={p.confusion} positive="not supported" negative="supported" />
                    <div style={{ fontSize: 12.5, color: TH.mut, marginTop: 8, lineHeight: 1.5 }}>
                      Precision {ciText(p.confusion.tp, p.confusion.tp + p.confusion.fp)} · recall {ciText(p.confusion.tp, p.confusion.tp + p.confusion.fn)}
                      {p.by_dataset ? Object.entries(p.by_dataset).map(([k, d]) => ` · ${DATASETS[k]?.[0] ?? k} (${d.n}): ${DATASETS[k]?.[1] ?? ''}`).join('') : ''}
                    </div>
                  </div>
                )}
              </div>
              {m.measured_on && <div style={{ fontSize: 12.5, color: TH.mut, marginTop: 14, lineHeight: 1.5 }}>Measured on {m.measured_on}.</div>}
            </More>
          )}
        </Section>

        {/* ---- 5. scope & audit ---- */}
        <Section id="scope" title="Scope & audit trail" sub="What a verdict means, and how to trace it back.">
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, 1fr)', gap: 12 }}>
            <div style={{ ...card, ...body }}>
              <div style={{ fontWeight: 700, fontSize: 15.5, marginBottom: 6 }}>What it checks</div>
              {[
                <><b>Sourcing, not truth.</b> It checks whether the cited sources carry the claims. A faithful quote of a false source is “supported”; the laundering and circle signals catch the common ways such sources are built.</>,
                <><b>Unknown stays neutral.</b> Spoken statements, paywalls and unreachable pages end as “not checkable”, never as a penalty.</>,
                <><b>The web changes.</b> A finished analysis replays exactly; a fresh run later can find new pages.</>,
              ].map((x, i) => <div key={i} style={{ padding: '8px 0', borderTop: i ? `1px solid ${TH.line}` : 'none', color: TH.mut }}>{x}</div>)}
            </div>
            <div style={{ ...card, ...body }}>
              <div style={{ fontWeight: 700, fontSize: 15.5, marginBottom: 6 }}>Audit trail</div>
              <div style={{ color: TH.mut }}>Every claim verdict is stored with its verbatim evidence passage (original and translation), its quote-match score, the model and the prompt version. Every step is cached by a hash of its input, so any analysis can be replayed and inspected.</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
                {[['Prompt version', m?.prompt_version || '–'], ['Quote match', `${fuzzy} / 100`]].map(([k, v]) => (
                  <div key={k}>
                    <div style={cap}>{k}</div>
                    <div style={{ fontFamily: MONO, fontWeight: 700, marginTop: 3 }}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <More title="Source labels: context shown on each source card">
            <div style={body}>
              {[
                ['Source type', 'Fixed rules on the URL and page markup: social media, academic, press release, official, reference, news, blog. It says what kind of document it is, not whether it is reliable.'],
                ['Wikipedia rating', 'The status the Wikipedia community gives the outlet on its Perennial Sources list, from a dated snapshot. Not listed is neutral.'],
                ['Popularity', 'Rank on the Tranco top-sites list. Popularity, not reliability.'],
                ['Re-registered', 'The Wayback Machine has pages from the domain at least a year before its current registration: an old name borrowed again.'],
              ].map(([k, v], i) => (
                <div key={k} style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '150px 1fr', gap: compact ? 3 : 12, padding: '8px 0', borderTop: i ? `1px solid ${TH.line}` : 'none' }}>
                  <b>{k}</b>
                  <span style={{ color: TH.mut }}>{v}</span>
                </div>
              ))}
              <div style={{ color: TH.mut, marginTop: 8 }}>Labels help the reader judge a source; they never change a verdict.</div>
            </div>
          </More>
        </Section>

        {/* ---- 6. tech stack ---- */}
        <Section id="stack" title="Tech stack" sub="Language models read and quote; everything that decides is plain code.">
          <div style={{ ...card, padding: '6px 20px' }}>
            {STACK.map(([k, v], i) => (
              <div key={k} style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '150px 1fr', gap: compact ? 3 : 12, padding: '10px 0', borderTop: i ? `1px solid ${TH.line}` : 'none', ...body }}>
                <b>{k}</b>
                <span style={{ color: TH.mut }}>{v}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  );
}
