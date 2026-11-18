import { useEffect, type CSSProperties } from 'react';
import { Wordmark } from './Brand';
import { useCompact } from './responsive';
import { CL, TH } from './theme';

// About us: who built CiteTrace, and that it won Junction 2026. Reached from the landing page ("About us").

const LINK: CSSProperties = { color: TH.ink, fontWeight: 600 };
const P: CSSProperties = { fontSize: 18, lineHeight: 1.6, margin: '0 0 20px' };
const LI = {
  louis: 'https://www.linkedin.com/in/louis-immanuel-dengler-3abb6a446',
  leonard: 'https://www.linkedin.com/in/leo-de',
};

export function About() {
  const compact = useCompact();
  useEffect(() => { window.scrollTo(0, 0); }, []);

  return (
    <div style={{ minHeight: '100dvh', background: '#fafaf9', color: TH.ink, fontFamily: TH.font }}>
      <div aria-hidden style={{ position: 'fixed', top: 0, bottom: 0, left: 0, width: compact ? 6 : 14, background: `linear-gradient(to bottom, ${CL.r}, ${CL.o}, ${CL.y}, ${CL.g})` }} />
      <div style={{ maxWidth: 760, margin: '0 auto', padding: compact ? 'max(18px, env(safe-area-inset-top)) 16px 40px 24px' : '28px 32px 48px 56px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700, fontSize: 19, letterSpacing: '-.02em' }}>
          <Wordmark />
          <a href="#/" style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 600, color: TH.mut, textDecoration: 'none', padding: '6px 4px' }}>Back</a>
        </div>

        <h1 style={{ margin: '48px 0 20px', fontSize: compact ? 36 : 48, fontWeight: 700, lineHeight: 1.1, letterSpacing: '-.04em' }}>About us</h1>

        <p style={P}>
          CiteTrace shows where a news article&apos;s claims really come from. Paste an article, and it finds the central claims,
          follows each one to the sources it cites, and checks whether those sources say what the article says.
        </p>
        <p style={P}>
          <strong>CiteTrace was the winning project of Junction 2026.</strong> It was built during the hackathon by{' '}
          <a href={LI.louis} target="_blank" rel="noopener noreferrer" style={LINK}>Louis</a>
          {' and '}
          <a href={LI.leonard} target="_blank" rel="noopener noreferrer" style={LINK}>Leonard</a>.
        </p>
      </div>
    </div>
  );
}
