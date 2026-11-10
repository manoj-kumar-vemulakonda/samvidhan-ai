// main.js - starts the page and switches between screens (the part after # in the address).
//   #/                      home (with the drop box: a pile of files -> sorted into cases -> "Approve all")
//   #/chat/7                a general chat
//   #/case/3                a case (its chat)      #/case/3/chat/9  #/case/3/files  /review/2  /timeline  /details
//   #/library               law library            #/library/act/BNS  #/library/constitution  #/library/judgments
//   #/library/courts        every court, its rule books, what is missing, "Add a rule book"
//   #/convert?q=IPC 420     old -> new law
//   #/workflows             Skills
//   #/settings

import { $, S, api, closeReader, esc, spinner, toastError } from './core.js';
import { onMessage, onPlan, onRun, onUpload } from './chat.js';
import { onReview, refreshFilesIfOpen, renderCase } from './case.js';
import { onIntake, onLibrary, onboarding, renderConvert, renderHome, renderLibrary, renderSettings, renderWorkflows } from './pages.js';
import { connectLive, initRight, initSidebar, loadAgenda, loadSidebar, renderSidebar } from './side.js';

function parseRoute() {
  const raw = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  const [path, query = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean);
  const params = new URLSearchParams(query);
  if (parts[0] === 'case' && parts[1]) {
    const tab = parts[2] || 'chat';
    return { view: 'case', caseId: Number(parts[1]), tab, sub: parts[3] || null, chatId: tab === 'chat' ? Number(parts[3]) || null : null };
  }
  if (parts[0] === 'chat' && parts[1]) return { view: 'chat', chatId: Number(parts[1]) };
  if (parts[0] === 'library') return { view: 'library', act: parts[1] === 'act' ? parts[2] : parts[1] || null, section: params.get('s') };
  if (['convert', 'workflows', 'settings'].includes(parts[0])) return { view: parts[0], q: params.get('q') || '' };
  return { view: 'home' };
}

let rendering = 0;
async function route() {
  const r = parseRoute();
  S.route = r;
  document.body.classList.remove('show-left');
  renderSidebar();
  const root = $('#view');
  const ticket = ++rendering;
  root.innerHTML = `<div class="loading">${spinner('Loading…')}</div>`;
  try {
    if (r.view === 'case') await renderCase(root, r.caseId, r.tab, r.sub);
    else if (r.view === 'chat') {
      const { renderChat } = await import('./chat.js');
      await renderChat(root, r.chatId);
      window.dispatchEvent(new CustomEvent('chat-ready'));
    } else if (r.view === 'library') await renderLibrary(root, r.act, r.section);
    else if (r.view === 'convert') renderConvert(root, r.q);
    else if (r.view === 'workflows') await renderWorkflows(root);
    else if (r.view === 'settings') await renderSettings(root);
    else await renderHome(root);
  } catch (error) {
    if (ticket === rendering) root.innerHTML = `<div class="empty-chat"><h3>Couldn't open this</h3><p class="warn">${esc(error.message)}</p><a class="btn" href="#/">Home</a></div>`;
  }
}

function statusBadge() {
  const s = S.status;
  const badge = $('#ai-badge');
  badge.textContent = s.ai_enabled ? `AI on · ${s.engine === 'claude_code' ? 'Claude Code' : 'API'}` : 'Search-only';
  badge.className = `ai-badge ${s.ai_enabled ? 'on' : 'off'}`;
  badge.title = s.ai_enabled ? s.engine_label : 'No Claude Code and no API key found: answers show sources only.';
}

async function start() {
  try {
    [S.status, S.settings] = await Promise.all([api('/api/status'), api('/api/settings')]);
  } catch (error) {
    $('#view').innerHTML = `<div class="empty-chat"><h3>The app isn't responding</h3><p>Start it with start.bat, then reload this page.</p></div>`;
    return;
  }
  statusBadge();
  initSidebar();
  initRight();
  await loadSidebar().catch(toastError);
  loadAgenda().catch(toastError);
  connectLive({
    upload: onUpload,
    message: onMessage,
    plan: onPlan,
    run: onRun,
    review: onReview,
    library: onLibrary,
    intake: onIntake,
    documents: () => refreshFilesIfOpen(),
    settings: async () => { S.settings = await api('/api/settings'); },
  });
  window.addEventListener('hashchange', () => { closeReader(); route(); });
  window.addEventListener('case-files-changed', () => { loadSidebar().catch(() => {}); loadAgenda().catch(() => {}); });
  window.addEventListener('calendar-changed', () => refreshFilesIfOpen());
  window.addEventListener('chat-updated', () => loadSidebar().catch(() => {}));
  setInterval(() => { loadAgenda().catch(() => {}); }, 5 * 60 * 1000);  // keep "today / tomorrow" fresh
  await route();
  if (!S.settings.onboarded) onboarding();
  // The law library's meaning index is built in the background on the very first start
  if (S.status.law_index && S.status.law_index.status === 'building') {
    $('#index-note').hidden = false;
    const poll = setInterval(async () => {
      const s = await api('/api/status').catch(() => null);
      if (!s) return;
      $('#index-note').textContent = `Preparing the law library search: ${Math.round((s.law_index?.progress || 0) * 100)}% (first start only)`;
      if (s.law_index?.status !== 'building') { $('#index-note').hidden = true; clearInterval(poll); }
    }, 5000);
  }
}

start();
