import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { About } from './About';
import { Landing } from './Landing';
import { Methodology } from './Methodology';
import './styles.css';

// Hash routes: #/ landing, #/methodology, #/trace?url=<article url>[&q=<question>], #/trace?demo=<key> or #/trace?job=<id> (uploaded PDF) analysis view.
function Root() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  if (hash.startsWith('#/methodology')) return <Methodology />;
  if (hash.startsWith('#/about')) return <About />;
  if (!hash.startsWith('#/trace')) return <Landing />;
  const q = new URLSearchParams(hash.split('?')[1] ?? '');
  return <App key={hash} initial={{ url: q.get('url') ?? undefined, demo: q.get('demo') ?? undefined, question: q.get('q') ?? undefined, job: q.get('job') ?? undefined }} />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
