// core.js - shared helpers: talking to the server, safe HTML, Markdown + citation chips,
// pop-up messages (toasts), dialogs (modals) and the side reader.

export const S = {
  status: null,        // /api/status
  settings: null,      // /api/settings
  cases: [],           // the left column
  generalChats: [],
  route: { view: 'home' },
  casePage: null,      // the open case (details, chats)
  chatPage: null,      // the open chat (messages)
  judgments: [],       // landmark-judgment notes (to recognise [Puttaswamy (2017)])
};

// ---------------------------------------------------------------------------
// Server calls
// ---------------------------------------------------------------------------
export async function api(path, options = {}) {
  const init = { method: options.method || 'GET', headers: {} };
  if (options.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (data.detail) message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch (_) { /* not JSON */ }
    throw new Error(message);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}

// POST that answers with Server-Sent Events (streamed answers). handlers = {eventName: fn(data)}
export async function streamPost(path, body, handlers, signal) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  if (!response.ok) {
    let message = `${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = 'message';
      const dataLines = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim();
        else if (line.startsWith('data: ')) dataLines.push(line.slice(6));
      }
      if (!dataLines.length) continue;
      let data;
      try { data = JSON.parse(dataLines.join('\n')); } catch (_) { continue; }
      if (handlers[event]) handlers[event](data);
    }
  }
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
export function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function h(html) {
  const template = document.createElement('template');
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export function parseISO(iso) {
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d);
}
export function todayISO() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}
export function isoOf(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
export function niceDate(iso, withDay = true) {
  if (!iso) return '';
  const d = parseISO(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const text = `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  return withDay ? `${DAYS[d.getDay()]} ${text}` : text;
}
export function relativeDay(iso) {
  const days = Math.round((parseISO(iso) - parseISO(todayISO())) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  if (days === -1) return 'Yesterday';
  if (days > 1 && days < 7) return DAYS[parseISO(iso).getDay()];
  return `${parseISO(iso).getDate()} ${MONTHS[parseISO(iso).getMonth()]}`;
}
export function daysUntil(iso) {
  return Math.round((parseISO(iso) - parseISO(todayISO())) / 86400000);
}
export function timeAgo(stamp) {
  if (!stamp) return '';
  const seconds = (Date.now() - new Date(stamp).getTime()) / 1000;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return niceDate(stamp.slice(0, 10), false);
}
export const KIND_LABEL = { hearing: 'Hearing', deadline: 'Deadline', limitation: 'Limitation', meeting: 'Meeting', reminder: 'Reminder', other: 'Other' };

// ---------------------------------------------------------------------------
// Toasts (small pop-up messages) and dialogs
// ---------------------------------------------------------------------------
export function toast(message, kind = 'info', ms = 4500) {
  const box = h(`<div class="toast ${kind}" role="status">${esc(message)}</div>`);
  $('#toasts').append(box);
  setTimeout(() => box.classList.add('leaving'), ms);
  setTimeout(() => box.remove(), ms + 400);
}
export function toastError(error) {
  toast(error?.message || String(error), 'error', 7000);
}

export function modal(title, bodyHtml, { wide = false, onClose } = {}) {
  const box = h(`<div class="modal-backdrop" role="dialog" aria-modal="true">
      <div class="modal ${wide ? 'wide' : ''}">
        <header><h2>${esc(title)}</h2><button class="icon-btn close" title="Close" aria-label="Close">✕</button></header>
        <div class="modal-body">${bodyHtml}</div>
      </div></div>`);
  const close = () => { box.remove(); document.removeEventListener('keydown', onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  $('.close', box).onclick = close;
  box.addEventListener('mousedown', (e) => { if (e.target === box) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(box);
  const first = $('input, textarea, select', box);
  if (first) setTimeout(() => first.focus(), 30);
  return { root: box, body: $('.modal-body', box), close };
}

export function confirmBox(question, okLabel = 'Yes') {
  return new Promise((resolve) => {
    const m = modal('Please confirm', `<p>${esc(question)}</p>
      <div class="actions"><button class="btn" data-no>Cancel</button><button class="btn primary" data-yes>${esc(okLabel)}</button></div>`,
    { onClose: () => resolve(false) });
    $('[data-no]', m.root).onclick = () => { m.close(); };
    $('[data-yes]', m.root).onclick = () => { resolve(true); m.root.remove(); };
  });
}

export function spinner(text = '') {
  return `<span class="spinner" aria-hidden="true"></span>${text ? ` <span>${esc(text)}</span>` : ''}`;
}

export async function download(url, fallbackName = 'download') {
  const response = await fetch(url);
  if (!response.ok) {
    let message = response.statusText;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const saved = response.headers.get('X-Saved-To');
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="([^"]+)"/);
  const blob = await response.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = match ? match[1] : fallbackName;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 4000);
  return saved;
}

// ---------------------------------------------------------------------------
// Markdown -> safe HTML, with citation chips
// ---------------------------------------------------------------------------
// statusOf(text) -> 'verified' | 'library' | 'unknown' | 'outside' | null (null = still checking)
function chipFor(inner, citeIndex) {
  const doc = inner.match(/^doc(?:ument)?\s*(\d+)(?:\s*,\s*(?:pp?|page)\.?\s*(\d+))?/i);
  const info = citeIndex ? citeIndex.get(inner) : null;
  if (doc) {
    const status = info ? info.status : 'pending';
    const page = doc[2] || '';
    return `<button class="chip doc ${status}" data-doc="${doc[1]}" data-page="${page}" title="Open this page of the document">📄 ${esc(inner)}</button>`;
  }
  if (/^[A-Z0-9 ,.'/&-]{3,}$/.test(inner) && !/\d/.test(inner) && inner === inner.toUpperCase()) {
    return `<span class="placeholder" title="Fill this in">[${esc(inner)}]</span>`;
  }
  if (!info && citeIndex) return null; // an ordinary bracket that isn't a citation
  const status = info ? info.status : 'pending';
  const id = info ? info.id : '';
  const kind = info ? info.kind : '';
  const titles = {
    verified: 'Found in the sources given to the AI - click to read it',
    library: 'In the law library but not among this answer\'s sources - click to check it',
    unknown: 'Not found in the library - treat with caution',
    outside: 'An Act that is not in the library - verify it yourself',
    pending: 'Checking...',
  };
  return `<button class="chip ${status} ${kind}" data-cite-id="${esc(id)}" data-cite-kind="${esc(kind)}" data-cite-text="${esc(inner)}" title="${titles[status] || ''}">${esc(inner)}</button>`;
}

function inline(text, citeIndex) {
  // text is already escaped
  let out = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
  out = out.replace(/\[([^\[\]\n]{2,90})\]/g, (whole, inner) => {
    const raw = inner.replace(/&amp;/g, '&').replace(/&#39;/g, "'").replace(/&quot;/g, '"');
    const chip = chipFor(raw, citeIndex);
    return chip === null ? whole : chip;
  });
  return out;
}

export function citeIndexFrom(report) {
  if (!report || !report.citations) return null;
  const map = new Map();
  for (const c of report.citations) map.set(c.text, c);
  return map;
}

export function markdown(source, report = null, streaming = false) {
  const citeIndex = streaming ? null : citeIndexFrom(report);
  const lines = String(source || '').replace(/\r/g, '').split('\n');
  const html = [];
  let list = null; // 'ul' | 'ol'
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) { html.push(`<p>${inline(esc(paragraph.join(' ')), citeIndex)}</p>`); paragraph = []; }
  };
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush(); closeList();
      const cells = (row) => row.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const head = cells(line);
      i += 1;
      const rows = [];
      while (i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i + 1])) { i += 1; rows.push(cells(lines[i])); }
      html.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(esc(c), citeIndex)}</th>`).join('')}</tr></thead><tbody>${
        rows.map((r) => `<tr>${r.map((c) => `<td>${inline(esc(c), citeIndex)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)/);
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)/);
    const quote = line.match(/^\s*>\s?(.*)/);
    if (!line.trim()) { flush(); closeList(); continue; }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flush(); closeList(); html.push('<hr>'); continue; }
    if (heading) {
      flush(); closeList();
      const level = Math.min(4, heading[1].length + 1);
      html.push(`<h${level}>${inline(esc(heading[2]), citeIndex)}</h${level}>`);
    } else if (bullet) {
      flush();
      if (list !== 'ul') { closeList(); html.push('<ul>'); list = 'ul'; }
      html.push(`<li>${inline(esc(bullet[1]), citeIndex)}</li>`);
    } else if (numbered) {
      flush();
      if (list !== 'ol') { closeList(); html.push(`<ol start="${numbered[1]}">`); list = 'ol'; }
      html.push(`<li>${inline(esc(numbered[2]), citeIndex)}</li>`);
    } else if (quote) {
      flush(); closeList();
      html.push(`<blockquote>${inline(esc(quote[1]), citeIndex)}</blockquote>`);
    } else {
      if (list && /^\s{2,}/.test(line)) {
        html[html.length - 1] = html[html.length - 1].replace(/<\/li>$/, ` ${inline(esc(line.trim()), citeIndex)}</li>`);
      } else {
        closeList();
        paragraph.push(line.trim());
      }
    }
  }
  flush(); closeList();
  return html.join('\n');
}

// ---------------------------------------------------------------------------
// The reader: a panel that slides in from the right to show a law, an Article,
// a judgment note or a page of one of your documents.
// ---------------------------------------------------------------------------
export function openReader(title, bodyHtml, { wide = false } = {}) {
  let panel = $('#reader');
  panel.classList.toggle('wide', wide);
  panel.innerHTML = `<header><h3>${esc(title)}</h3><button class="icon-btn" data-close title="Close" aria-label="Close">✕</button></header>
    <div class="reader-body">${bodyHtml}</div>`;
  panel.hidden = false;
  void panel.offsetWidth; // let the browser place it off-screen first, so it slides in
  panel.classList.add('open');
  $('[data-close]', panel).onclick = closeReader;
  return $('.reader-body', panel);
}
export function closeReader() {
  const panel = $('#reader');
  panel.classList.remove('open');
  // hide after the slide-out, unless something was opened again meanwhile (e.g. a link to a section)
  setTimeout(() => { if (!panel.classList.contains('open')) panel.hidden = true; }, 200);
}

// Bare-act texts often break lines in the middle of a sentence: join those lines again
export function reflow(text) {
  return String(text || '').replace(/[ \t]*\n(?=[a-z“"‘'])/g, ' ').replace(/([a-z,])[ \t]*\n(?=[A-Za-z])/g, '$1 ');
}
export function textBlocks(text) {
  return reflow(text).split(/\n+/).filter((p) => p.trim()).map((p) => `<p>${esc(p)}</p>`).join('');
}

export async function showSection(id) {
  const body = openReader('Loading...', spinner());
  try {
    const s = await api(`/api/laws/sections/${encodeURIComponent(id)}`);
    const related = (s.related || []).map((m) => m.section_id
      ? `<button class="chip verified" data-cite-id="${esc(m.section_id)}" data-cite-kind="section">${esc(m.label)}</button> <span class="muted">${esc(m.status)}</span>`
      : `<span class="muted">${esc(m.label || '')} ${esc(m.status)}</span>`).join('<br>');
    $('#reader h3').textContent = `${s.label} - ${s.title}`;
    const classification = (s.classification || []).map((c) => `<div class="classification">
        <button class="chip verified" data-cite-id="${esc(c.id)}" data-cite-kind="section">${esc(c.label)}</button>
        ${esc(c.text.split('\n').filter((line) => /^(Cognizable|Bailable|By what court)/.test(line)).join(' · '))}</div>`).join('');
    const court = s.book_kind && s.book_kind !== 'act'
      ? `<p class="court-of"><span class="badge">${esc(s.book_kind === 'handbook' ? 'Practice handbook' : 'Court rules')}</span> ${esc(s.court || '')}${s.jurisdiction ? ` · applies in ${esc(s.jurisdiction)}` : ''}</p>` : '';
    body.innerHTML = `<p class="muted">${esc(s.location)}</p>${court}
      ${s.act_status === 'repealed' ? `<p class="warn">⚠ ${esc(s.act_note)}</p>` : (s.act_note ? `<p class="muted">${esc(s.act_note)}</p>` : '')}
      ${s.status === 'omitted' ? '<p class="warn">This provision is omitted / repealed.</p>' : ''}
      <div class="law-text">${textBlocks(s.text)}</div>
      ${classification ? `<h4>Bailable? Cognizable? Which court? (BNSS First Schedule)</h4>${classification}` : ''}
      ${related ? `<h4>Old / new law</h4><p>${related}</p>` : ''}
      <p class="muted small">Source: ${esc(s.source)}${s.as_on ? ` · as on ${esc(s.as_on)}` : ''}</p>
      <div class="actions left">
        ${s.pdf_url ? `<a class="btn small" href="${esc(s.pdf_url)}" target="_blank" rel="noopener">📄 Official PDF${s.page ? `, page ${esc(s.page)}` : ''}</a>` : ''}
        <a class="btn small" href="#/library/act/${encodeURIComponent(s.act)}?s=${encodeURIComponent(s.id)}">Open in the law library</a></div>`;
  } catch (error) { body.innerHTML = `<p class="warn">${esc(error.message)}</p>`; }
}

export async function showArticle(id) {
  const body = openReader('Loading...', spinner());
  try {
    const a = await api(`/api/articles/${encodeURIComponent(id)}`);
    $('#reader h3').textContent = `${a.label} - ${a.title}`;
    body.innerHTML = `<p class="muted">${esc(a.location || '')}</p>
      ${a.status === 'omitted' ? '<p class="warn">Omitted - no longer in force.</p>' : ''}
      ${(a.notes || []).length ? `<div class="court-notes"><strong>Court ruling (official footnote):</strong> ${a.notes.map(esc).join(' ')}</div>` : ''}
      <div class="law-text">${textBlocks(a.text)}</div>
      <p class="muted small">${esc(S.status?.source || 'The Constitution of India (official text)')}</p>`;
  } catch (error) { body.innerHTML = `<p class="warn">${esc(error.message)}</p>`; }
}

export async function showJudgment(id) {
  const body = openReader('Loading...', spinner());
  try {
    const c = await api(`/api/judgments/${encodeURIComponent(id)}`);
    $('#reader h3').textContent = c.name;
    body.innerHTML = `<p class="muted">${esc(c.year)}${c.bench ? ` · ${esc(c.bench)}` : ''}${c.citation ? ` · ${esc(c.citation)}` : ''}</p>
      <h4>Held</h4><p>${esc(c.held)}</p>${c.later ? `<h4>Later</h4><p>${esc(c.later)}</p>` : ''}
      <p class="muted small">A short note written for this app - read the full judgment before relying on it.</p>
      <a class="btn small" href="${esc(c.link)}" target="_blank" rel="noopener">Find the full judgment ↗</a>`;
  } catch (error) { body.innerHTML = `<p class="warn">${esc(error.message)}</p>`; }
}

export async function showDocument(id, page = 1) {
  const body = openReader('Loading...', spinner(), { wide: true });
  try {
    const d = await api(`/api/documents/${id}`);
    page = Math.min(Math.max(1, Number(page) || 1), d.pages || 1);
    $('#reader h3').textContent = d.title;
    const isPdf = /\.pdf$/i.test(d.filename || d.path || '');
    const isImage = /\.(png|jpe?g|webp|bmp|tiff?)$/i.test(d.filename || '');
    const fileUrl = `/api/documents/${id}/file`;
    body.innerHTML = `<p class="muted">${esc(d.doc_type || 'Document')}${d.doc_date ? ' · ' + esc(niceDate(d.doc_date, false)) : ''} · ${d.pages} page${d.pages === 1 ? '' : 's'}${d.folder ? ` · 📁 ${esc(d.folder)}` : ''}</p>
      ${d.summary ? `<p>${esc(d.summary)}</p>` : ''}
      <div class="doc-toolbar">
        <a class="btn small" href="${fileUrl}${isPdf ? '#page=' + page : ''}" target="_blank" rel="noopener">Open file ↗</a>
        <span class="muted small">${esc(d.filename || '')}</span>
      </div>
      ${isPdf ? `<iframe class="doc-frame" title="Document" src="${fileUrl}#page=${page}"></iframe>` : ''}
      ${isImage ? `<img class="doc-image" alt="${esc(d.title)}" src="${fileUrl}">` : ''}
      <div class="page-nav"><button class="btn small" data-prev>‹</button> <span>Page <b data-num>${page}</b> of ${d.pages}</span> <button class="btn small" data-next>›</button> <span class="muted small">(text read from the file)</span></div>
      <div class="page-text"></div>`;
    const show = (n) => {
      page = Math.min(Math.max(1, n), d.pages || 1);
      $('[data-num]', body).textContent = page;
      const p = (d.page_texts || []).find((x) => x.page === page);
      $('.page-text', body).innerHTML = p && p.text.trim() ? textBlocks(p.text) : '<p class="muted">No readable text on this page.</p>';
      const frame = $('.doc-frame', body);
      if (frame) frame.src = `${fileUrl}#page=${page}`;
    };
    $('[data-prev]', body).onclick = () => show(page - 1);
    $('[data-next]', body).onclick = () => show(page + 1);
    show(page);
  } catch (error) { body.innerHTML = `<p class="warn">${esc(error.message)}</p>`; }
}

// One click handler for every chip on the page
document.addEventListener('click', (event) => {
  const chip = event.target.closest('.chip');
  if (!chip) return;
  if (chip.dataset.doc) { event.preventDefault(); showDocument(chip.dataset.doc, chip.dataset.page); return; }
  const id = chip.dataset.citeId;
  const kind = chip.dataset.citeKind;
  if (!id) {
    if (chip.dataset.citeText) toast('This citation could not be matched to a law in the library - verify it yourself.', 'warn');
    return;
  }
  event.preventDefault();
  if (kind === 'section' || id.includes(':')) showSection(id);
  else if (kind === 'judgment' || id.startsWith('case-')) {
    if (id.startsWith('case?')) toast('This judgment is not in the app\'s library - verify it yourself.', 'warn');
    else showJudgment(id);
  } else if (id.startsWith('doc:')) showDocument(id.slice(4));
  else showArticle(id);
});
