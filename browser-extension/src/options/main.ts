import { DEFAULT_API_BASE, getApiBase, normaliseApiBase, originPattern, setApiBase } from '../config';
import { BACKEND_HOSTS } from '../manifest';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const input = $<HTMLInputElement>('base');
const status = $<HTMLDivElement>('status');
$('default').textContent = DEFAULT_API_BASE;
const say = (text: string, ok: boolean) => { status.textContent = text; status.className = ok ? 'ok' : 'bad'; };

void getApiBase().then((b) => { input.value = b; });

async function test(base: string) {
  try {
    const r = await fetch(`${base}/api/health`);
    const b = await r.json();
    say(b?.ok ? `Connected to ${base} (version ${String(b.version ?? '?').slice(0, 7)}).` : `${base} answered, but not like a Source Check server.`, !!b?.ok);
  } catch {
    say(`Saved, but ${base} could not be reached.`, false);
  }
}

$('save').addEventListener('click', async () => {
  const base = normaliseApiBase(input.value);
  if (!base) return say('Enter an http(s) address, e.g. https://sourcecheck.tech', false);
  const pattern = originPattern(base);
  const known = BACKEND_HOSTS.some((h) => pattern && h.startsWith(pattern.replace('/*', '')));
  if (pattern && !known) {
    // must stay the first await: the permission prompt needs the click
    const granted = await chrome.permissions.request({ origins: [pattern] }).catch(() => false);
    if (!granted) return say('Without permission for that address the extension cannot reach the server.', false);
  }
  await setApiBase(base === DEFAULT_API_BASE ? null : base);
  input.value = base;
  await test(base);
});

$('reset').addEventListener('click', async () => {
  await setApiBase(null);
  input.value = DEFAULT_API_BASE;
  await test(DEFAULT_API_BASE);
});
