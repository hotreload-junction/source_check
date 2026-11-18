// The extension manifest, generated per browser at build time (vite.config.ts writes dist/<target>/manifest.json).
// Chrome / Edge / Brave: Manifest V3 + chrome.sidePanel. Firefox: the same code in a sidebar (sidebar_action), and an
// event-page background (Firefox MV3 has no extension service workers).
export type Target = 'chrome' | 'firefox';

/** Backends the extension may call without asking: the deployed site, its preview and a local dev backend. Any other
 * backend URL set on the options page is requested at runtime (optional_host_permissions). */
// (match patterns without a port match every port, so a local backend on :8000 is covered)
export const BACKEND_HOSTS = ['https://sourcecheck.tech/*', 'https://preview.sourcecheck.tech/*', 'http://localhost/*', 'http://127.0.0.1/*'];

/** `e2e`: test builds only (tests/e2e.cjs) get access to every site, because automation cannot click the toolbar
 * icon that grants activeTab. Never set for a build that is shipped. */
export function buildManifest(target: Target, version: string, e2e = false): Record<string, unknown> {
  const icons = { 16: 'icons/icon-16.png', 32: 'icons/icon-32.png', 48: 'icons/icon-48.png', 128: 'icons/icon-128.png' };
  const base: Record<string, unknown> = {
    manifest_version: 3,
    name: 'Source Check',
    short_name: 'Source Check',
    version,
    description: 'Checks whether a news article\'s sources really say what the article claims. Click the icon, or select a claim and right-click.',
    icons,
    action: { default_title: 'Check this article with Source Check', default_icon: icons },
    // activeTab: reading the page needs no standing access to every site. It is granted when the reader clicks the
    // toolbar icon or a context-menu entry; the "Check this page" button in the panel asks for that one site instead.
    permissions: ['activeTab', 'scripting', 'contextMenus', 'storage'],
    host_permissions: e2e ? [...BACKEND_HOSTS, '<all_urls>'] : BACKEND_HOSTS,
    optional_host_permissions: ['https://*/*', 'http://*/*'],
    options_ui: { page: 'options.html', open_in_tab: false },
    commands: {
      _execute_action: { suggested_key: { default: 'Alt+Shift+S' }, description: 'Check this article' },
    },
  };
  if (target === 'chrome') {
    return {
      ...base,
      minimum_chrome_version: '116',            // chrome.sidePanel.open()
      permissions: [...(base.permissions as string[]), 'sidePanel'],
      side_panel: { default_path: 'sidepanel.html' },
      background: { service_worker: 'background.js', type: 'module' },
    };
  }
  return {
    ...base,
    sidebar_action: { default_panel: 'sidepanel.html', default_title: 'Source Check', default_icon: icons, open_at_install: false },
    background: { scripts: ['background.js'], type: 'module' },
    browser_specific_settings: { gecko: { id: 'source-check@sourcecheck.tech', strict_min_version: '121.0' } },
  };
}
