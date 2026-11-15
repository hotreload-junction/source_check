import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { Landing } from './Landing';
import { Methodology } from './Methodology';
import './styles.css';

// Hash routes: #/ landing, #/methodology, #/trace?url=<article url>[&q=<question>] or #/trace?demo=<key> analysis view.
function Root() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  if (hash.startsWith('#/methodology')) return <Methodology />;
  if (!hash.startsWith('#/trace')) return <Landing />;
  const q = new URLSearchParams(hash.split('?')[1] ?? '');
  return <App key={hash} initial={{ url: q.get('url') ?? undefined, demo: q.get('demo') ?? undefined, question: q.get('q') ?? undefined }} />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
