// Background (service worker in Chrome, event page in Firefox): toolbar icon and context menu.
// Opening the side panel is only allowed directly inside the user's click, so it happens FIRST and synchronously,
// before anything is awaited; the check itself runs afterwards.
import { startCheck } from './check';
import { dropState, getState, sameDocument } from './state';

const MENU_PAGE = 'source-check-page';
const MENU_SELECTION = 'source-check-selection';

declare const browser: { sidebarAction?: { open(): Promise<void> } } | undefined;

function openPanel(tab: chrome.tabs.Tab): void {
  try {
    if (chrome.sidePanel?.open) {
      if (tab.id != null) chrome.sidePanel.open({ tabId: tab.id }).catch((e) => console.warn('side panel', e));
      else if (tab.windowId != null) chrome.sidePanel.open({ windowId: tab.windowId }).catch((e) => console.warn('side panel', e));
    } else if (typeof browser !== 'undefined' && browser?.sidebarAction) {
      browser.sidebarAction.open().catch((e) => console.warn('sidebar', e));     // Firefox
    }
  } catch (e) {
    console.warn('could not open the panel', e);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_PAGE, title: 'Check this article with Source Check', contexts: ['page'] });
    chrome.contextMenus.create({ id: MENU_SELECTION, title: 'Check “%s” with Source Check', contexts: ['selection'] });
  });
});

// the toolbar icon starts a check (not only opens the panel), so openPanelOnActionClick stays off
chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: false }).catch(() => undefined);

chrome.action.onClicked.addListener((tab) => {
  openPanel(tab);
  if (tab.id == null) return;
  void startCheck({ tabId: tab.id, url: tab.url, title: tab.title, selection: false });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab) return;
  openPanel(tab);
  if (tab.id == null) return;
  const selection = info.menuItemId === MENU_SELECTION;
  void startCheck({ tabId: tab.id, url: tab.url ?? info.pageUrl, title: tab.title, selection, selectionText: info.selectionText });
});

// a check belongs to the page it read: forget it when the tab goes to another page or closes
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (!change.url) return;
  void getState(tabId).then((s) => { if (s && s.phase !== 'reading' && !sameDocument(s.url, change.url)) void dropState(tabId); });
});
chrome.tabs.onRemoved.addListener((tabId) => { void dropState(tabId); });
