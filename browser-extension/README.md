# Source Check browser extension

Checks the article you are reading, where you read it.

- **Check this article**: click the Source Check icon in the toolbar (or `Alt+Shift+S`, or right-click the page →
  *Check this article with Source Check*). The side panel opens and the graph builds up as the check runs:
  article → claims → sources, with the verdicts and evidence passages, the same view as on sourcecheck.tech.
- **Check one claim**: select a sentence in the article, right-click → *Check “…” with Source Check*.
  The whole article is read as usual. The selected sentence is checked against its sources as the focus claim, with a
  dashed outline in the graph. It never changes the article verdict, which still comes from the article's central claims only
  (same rule as the question field on the website, DECISIONS F6).

## Install (development)

```bash
cd browser-extension
npm ci
npm run build             # -> dist/chrome and dist/firefox
```

- **Chrome / Edge / Brave** (Chrome 116+): `chrome://extensions` → Developer mode → *Load unpacked* → `dist/chrome`.
- **Firefox** (121+): `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on* → `dist/firefox/manifest.json`.
  The Firefox build uses a sidebar instead of the side panel. It builds, but nobody has tested it in Firefox yet.

The extension talks to `https://sourcecheck.tech` by default. To use another server (preview, a local backend
on `http://localhost:8000`), open the extension's options (gear icon in the panel). A build can also get a different
default: `VITE_API_BASE=https://preview.sourcecheck.tech npm run build`.

## How it works

| Part | File | What it does |
|---|---|---|
| Background | `src/background.ts` | Toolbar icon and context menu. Opens the panel right inside the click (browsers allow that only during a user gesture), then starts the check. |
| Start a check | `src/check.ts`, `src/page.ts` | Injects `readPage()` into the tab (`chrome.scripting`), which returns the rendered HTML (`outerHTML`) and the selection, then `POST /api/analyses/page`. The job id is kept per tab in `chrome.storage.session` (`src/state.ts`). |
| Side panel | `src/panel/` | Follows the active tab. *Home* shows the actions for this page, and a started check switches to the web app's result view. That view (`App`, graph, panels, methodology) is bundled from `../frontend/src` and pointed at the backend with `setApiBase`. |
| Options | `options.html`, `src/options/` | Backend address. A server that is not built in asks for host permission when you save it. |
| Manifest | `src/manifest.ts` | Generated per browser at build time (`vite.config.ts`). |

Backend side: `POST /api/analyses/page` (contracts/api.md), `backend/app/fetch/page.py` (HTML → markdown with links,
same trafilatura settings as the fallback fetch), `backend/app/pipeline/focus.py` (`locate_selection`: the
selection is located by code, then checked as a focus claim).

### Permissions and privacy

- `activeTab` + `scripting`: the page is read only after you click the icon or a context-menu entry for that tab.
  There is no standing access to the sites you visit. The panel's own *Check this article* button is not such a click,
  so the first time it asks for access to that one site.
- `host_permissions` only for the Source Check servers. Other servers are optional and asked for at runtime.
- What is sent: the page's HTML as your browser shows it (text, links, and the scripts' analytics IDs that the
  infrastructure signal reads), its address and title, and your selection. It is sent only when you start a check.
  This also works for pages our server cannot fetch itself (bot walls, consent pages). For a paywalled article
  you are logged into, the full text goes to the server: it is used for that analysis only and is stored with its result.
- The result cache key includes a hash of the text your browser sent. A page sent from someone's browser is
  never shown to someone who asks for the same URL on the website.

### Things to know when changing it

- `readPage` is serialised into the page on its own: it must not use anything outside its function body.
- `chrome.sidePanel.open()` / Firefox `sidebarAction.open()` only work synchronously inside the click handler.
  Await nothing before them.
- MV3 service workers sleep: listeners are registered at top level, and state lives in `chrome.storage.session`,
  not in variables.
- The side panel is narrow, so the shared web app renders in its compact (phone) layout. Changes to `frontend/src`
  show up here on the next build. `npm run typecheck` checks the shared files against this package's React types.

## Checks

- `npm run typecheck`, `npm test`, `npm run build`: run by `tools/check`.
- `tests/e2e.cjs`: smoke test in a real Chromium (not in `tools/check`, it needs a browser and a running backend).
  It loads the extension, opens a demo article (served locally under its real URL), checks it from the panel,
  then checks a selected sentence. Steps are in the file header.
