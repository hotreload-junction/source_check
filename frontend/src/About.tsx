import { useEffect, type CSSProperties, type ReactNode } from 'react';
import { Wordmark } from './Brand';
import { useCompact } from './responsive';
import { CL, TH } from './theme';

// About us: what Source Check is, how it works in plain words, the principles behind it, and who built it.
// Reached from the landing page ("About us"). The project is called Source Check everywhere on this page.

const GRAD = `linear-gradient(90deg, ${CL.r}, ${CL.o}, ${CL.y}, ${CL.g})`;
// Gradient outline on a white card: the padding box paints the fill, the border box the gradient behind the transparent border.
const gradBorder = (fill: string): CSSProperties => ({ border: '1.5px solid transparent', background: `linear-gradient(${fill}, ${fill}) padding-box, ${GRAD} border-box` });
const CARD: CSSProperties = { background: TH.card, border: `1px solid ${TH.line}`, boxShadow: TH.sh };

const LI = {
  louis: 'https://www.linkedin.com/in/louis-immanuel-dengler-3abb6a446',
  leonard: 'https://www.linkedin.com/in/leo-de',
};

const STEPS: { n: string; c: string; title: string; text: string }[] = [
  { n: '1', c: CL.r, title: 'Find the claims', text: 'Source Check reads the article and picks the central claims, the handful of statements the whole piece rests on. Every claim is tied to the exact sentence it comes from.' },
  { n: '2', c: CL.y, title: 'Follow the sources', text: 'It follows each link and attribution to the page behind it. If the article names a source but gives no link, Source Check searches for the original statement. It also follows what the sources themselves cite.' },
  { n: '3', c: CL.g, title: 'Show the proof', text: 'For every claim the AI must quote the passage that backs it up, word for word. Code then looks that quote up in the real page. If it is not there, it does not count.' },
];

const PRINCIPLES: { title: string; text: string }[] = [
  { title: 'Quote first, verdict second', text: 'An AI can sound sure and still be wrong. So no verdict stands on the AI’s word. It has to hand over the evidence sentence, and our code checks it against the fetched source.' },
  { title: 'Not knowing is not guilt', text: 'Dead links, paywalls, social-media posts and links to the article’s own site are treated as neutral. They never count against an article, because we simply could not read them.' },
  { title: 'Two signs, never one', text: 'An article is only flagged as a propaganda pattern when two separate warning signs agree, for example shared site infrastructure and a citation circle. One weak claim is never enough.' },
  { title: 'Everything is auditable', text: 'Each verdict keeps its quote, its match score, the model and the prompt version. The methodology page shows how often we are right and wrong on labelled test cases, including the false positives.' },
];

const SIGNALS: { t: string; d: string }[] = [
  { t: 'Who runs the sites?', d: 'Shared analytics and ad IDs, very young domains and published lists of known networks show when “independent” sources have the same owner.' },
  { t: 'Do they only cite each other?', d: 'Source Check follows citations a step further. A source that leads back to the article, or to its own network, forms a circle.' },
  { t: 'Where does it really come from?', d: 'Articles that repackage Telegram or social-media posts as reporting are marked, so the original post is visible.' },
];

const STACK = ['Exa', 'Context.dev', 'Verda', 'MariaDB', 'Hostinger'];

function Eyebrow({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase', color: TH.mut, marginBottom: 12 }}>{children}</div>;
}

function Heading({ children, compact }: { children: ReactNode; compact: boolean }) {
  return <h2 style={{ margin: '0 0 28px', fontSize: compact ? 28 : 36, fontWeight: 700, lineHeight: 1.12, letterSpacing: '-.035em' }}>{children}</h2>;
}

function Person({ name, role, initial, href, c }: { name: string; role: string; initial: string; href: string; c: string }) {
  return (
    <div style={{ ...CARD, borderRadius: 22, padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <span aria-hidden style={{ flex: 'none', width: 64, height: 64, borderRadius: 20, background: TH.ink, display: 'grid', placeItems: 'center', fontSize: 28, fontWeight: 700, color: '#fff', boxShadow: `inset 0 -4px 0 ${c}` }}>{initial}</span>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-.02em' }}>{name}</div>
          <div style={{ fontSize: 15, color: TH.mut, marginTop: 2 }}>{role}</div>
        </div>
      </div>
      <a href={href} target="_blank" rel="noopener noreferrer" style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 12, background: TH.ink, color: '#fff', fontWeight: 600, fontSize: 14, textDecoration: 'none' }}>
        <svg aria-hidden viewBox="0 0 24 24" style={{ width: 16, height: 16, fill: '#fff' }}><path d="M4.98 3.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM3 9.75h4V21H3V9.75zm6.5 0h3.8v1.6h.05c.53-1 1.83-2.05 3.77-2.05 4.03 0 4.78 2.65 4.78 6.1V21h-4v-5.2c0-1.24-.02-2.84-1.73-2.84-1.73 0-2 1.35-2 2.75V21h-4V9.75z" /></svg>
        LinkedIn
        <span aria-hidden>↗</span>
      </a>
    </div>
  );
}

export function About() {
  const compact = useCompact();
  useEffect(() => { window.scrollTo(0, 0); }, []);

  const section: CSSProperties = { marginTop: compact ? 64 : 96 };

  return (
    <div style={{ minHeight: '100dvh', background: '#fafaf9', color: TH.ink, fontFamily: TH.font }}>
      <div style={{ maxWidth: 1040, margin: '0 auto', padding: compact ? 'max(18px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) max(40px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left))' : '36px 40px 72px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700, fontSize: 19, letterSpacing: '-.02em' }}>
          <a href="#/" title="Back to start" style={{ color: TH.ink, textDecoration: 'none' }}><Wordmark /></a>
          <a href="#/methodology" style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 600, letterSpacing: 0, color: TH.mut, textDecoration: 'none', padding: compact ? '10px 4px' : '6px 4px' }}>How it works</a>
          <a href="#/" style={{ marginLeft: compact ? 16 : 32, fontSize: 14, fontWeight: 600, letterSpacing: 0, color: TH.mut, textDecoration: 'none', padding: compact ? '10px 4px' : '6px 4px' }}>Back</a>
        </div>

        {/* hero */}
        <header style={{ textAlign: 'center', marginTop: compact ? 48 : 88 }}>
          <Eyebrow>About us</Eyebrow>
          <h1 style={{ margin: '0 auto', maxWidth: 820, fontSize: compact ? 'clamp(34px, 9vw, 52px)' : 'clamp(44px, 5.2vw, 68px)', fontWeight: 700, lineHeight: 1.05, letterSpacing: '-.045em' }}>
            Propaganda fakes its sources.{' '}
            <span style={{ color: CL.g, backgroundImage: GRAD, WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>We show you where.</span>
          </h1>
          <p style={{ margin: '28px auto 0', maxWidth: 680, fontSize: compact ? 18 : 21, lineHeight: 1.55, color: TH.mut }}>
            Source Check shows whether the sources of a claim really back up what it says. Paste an article and it points to the exact sentence
            in the source that proves the claim, or tells you that there is none.
          </p>
          <div style={{ marginTop: 32, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12 }}>
            <a href="#/" style={{ padding: '14px 22px', borderRadius: 14, background: TH.ink, color: '#fff', fontWeight: 600, fontSize: 16, textDecoration: 'none' }}>Check an article</a>
            <a href="#/methodology" style={{ padding: '14px 22px', borderRadius: 14, color: TH.ink, fontWeight: 600, fontSize: 16, textDecoration: 'none', ...gradBorder(TH.card) }}>See the methodology</a>
          </div>
        </header>

        {/* why */}
        <section style={{ ...section, display: 'grid', gridTemplateColumns: compact ? '1fr' : '5fr 7fr', gap: compact ? 20 : 56, alignItems: 'start' }}>
          <div>
            <Eyebrow>Why we built it</Eyebrow>
            <h2 style={{ margin: 0, fontSize: compact ? 28 : 36, fontWeight: 700, lineHeight: 1.12, letterSpacing: '-.035em' }}>A link is not the same as proof.</h2>
          </div>
          <div style={{ fontSize: 18, lineHeight: 1.65, color: '#2a2a31' }}>
            <p style={{ margin: '0 0 18px' }}>
              Most readers see a footnote or a link and assume the claim has been checked. Nobody has time to open every source and read it.
              That gap is exactly what disinformation uses: an article cites a source that says something different, a page that only cites the
              article back, or a channel that is run by the same people.
            </p>
            <p style={{ margin: 0 }}>
              Source Check does that reading for you. It follows the chain from a statement to its source and, where it can, on to the source of the source.
              It shows what it found and how sure it is, in the open, so you can judge for yourself.
            </p>
          </div>
        </section>

        {/* how */}
        <section style={section}>
          <Eyebrow>How it works</Eyebrow>
          <Heading compact={compact}>From claim to source in three steps.</Heading>
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(3, 1fr)', gap: 16 }}>
            {STEPS.map((s) => (
              <div key={s.n} style={{ ...CARD, borderRadius: 22, padding: 24 }}>
                <span aria-hidden style={{ display: 'grid', placeItems: 'center', width: 40, height: 40, borderRadius: 12, background: s.c, color: '#fff', fontWeight: 700, fontSize: 18 }}>{s.n}</span>
                <div style={{ margin: '18px 0 8px', fontSize: 20, fontWeight: 700, letterSpacing: '-.02em' }}>{s.title}</div>
                <p style={{ margin: 0, fontSize: 16, lineHeight: 1.6, color: TH.mut }}>{s.text}</p>
              </div>
            ))}
          </div>
        </section>

        {/* beyond the claim */}
        <section style={{ ...section, ...gradBorder('#fff'), borderRadius: 28, padding: compact ? 24 : 44 }}>
          <Eyebrow>Beyond a single claim</Eyebrow>
          <Heading compact={compact}>It also looks at who is behind the sources.</Heading>
          <p style={{ margin: '0 0 28px', maxWidth: 680, fontSize: 18, lineHeight: 1.6, color: TH.mut }}>
            One distorted claim can be a mistake. A whole set of sources that share an owner, cite each other and repeat the same channel is a pattern.
            That is why Source Check also looks at the websites behind the sources.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(3, 1fr)', gap: 24 }}>
            {SIGNALS.map((s, i) => (
              <div key={s.t} style={{ borderTop: `3px solid ${[CL.o, CL.y, CL.g][i]}`, paddingTop: 14 }}>
                <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: '-.01em' }}>{s.t}</div>
                <p style={{ margin: '6px 0 0', fontSize: 15.5, lineHeight: 1.6, color: TH.mut }}>{s.d}</p>
              </div>
            ))}
          </div>
        </section>

        {/* principles */}
        <section style={section}>
          <Eyebrow>What we stand for</Eyebrow>
          <Heading compact={compact}>Honest about what we can and cannot prove.</Heading>
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, 1fr)', gap: 16 }}>
            {PRINCIPLES.map((p) => (
              <div key={p.title} style={{ ...CARD, borderRadius: 22, padding: 24, display: 'flex', gap: 16 }}>
                <span aria-hidden style={{ flex: 'none', width: 6, borderRadius: 3, background: GRAD, backgroundSize: '100% 400%' }} />
                <div>
                  <div style={{ fontSize: 19, fontWeight: 700, letterSpacing: '-.02em' }}>{p.title}</div>
                  <p style={{ margin: '8px 0 0', fontSize: 16, lineHeight: 1.6, color: TH.mut }}>{p.text}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* team */}
        <section style={section}>
          <Eyebrow>The team</Eyebrow>
          <Heading compact={compact}>Built at Junction 2026.</Heading>
          <p style={{ margin: '0 0 28px', maxWidth: 680, fontSize: 18, lineHeight: 1.6, color: TH.mut }}>
            Source Check was the winning project of Junction 2026. Louis and Leonard built it during the hackathon for the Hostinger challenge,
            with a few AI coding agents helping on the side. It runs live on Hostinger.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : 'repeat(2, 1fr)', gap: 16 }}>
            <Person name="Louis" role="Frontend, deployment and evaluation" initial="L" href={LI.louis} c={CL.r} />
            <Person name="Leonard" role="Detection pipeline, prompts and models" initial="L" href={LI.leonard} c={CL.g} />
          </div>
          <div style={{ marginTop: 28, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 14, color: TH.mut, marginRight: 4 }}>Built with</span>
            {STACK.map((s) => (
              <span key={s} style={{ padding: '6px 14px', borderRadius: 999, background: TH.card, border: `1px solid ${TH.line}`, fontSize: 14, fontWeight: 600 }}>{s}</span>
            ))}
          </div>
        </section>

        <footer style={{ marginTop: compact ? 56 : 88, textAlign: 'center', fontSize: 14, color: TH.mut }}>
          Built with <span role="img" aria-label="love">♥️</span> by Louis &amp; Leonard @ Junction 2026
        </footer>
      </div>
    </div>
  );
}
