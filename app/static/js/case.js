// case.js - one case: its header, and the tabs Chat | Files | Review | Timeline | Details.

import {
  $, $$, KIND_LABEL, S, api, confirmBox, daysUntil, download, esc, h, markdown, modal, niceDate, relativeDay,
  showDocument, spinner, toast, toastError, todayISO,
} from './core.js';
import { renderChat, uploadFiles } from './chat.js';
import { eventDialog, loadSidebar } from './side.js';

let current = null; // {case, chats, tab, chatId, reviewId}

export async function renderCase(root, caseId, tab = 'chat', sub = null) {
  const data = await api(`/api/cases/${caseId}`);
  current = { ...data, tab, chatId: tab === 'chat' ? (Number(sub) || data.chats[0]?.id) : null, reviewId: tab === 'review' ? Number(sub) || null : null };
  S.casePage = current;
  const c = data.case;
  const next = c.next_event;
  root.innerHTML = `
    <div class="case-page">
      <header class="case-head">
        <div class="case-title-row">
          <h2>${esc(c.title)}</h2>${c.status === 'closed' ? '<span class="badge">Closed</span>' : ''}
          <span class="spacer"></span>
          <button class="btn small" data-open-folder title="Open the case folder on this PC">📂 Folder</button>
        </div>
        <div class="case-sub muted">${esc([c.court, c.case_number, c.client && `Client: ${c.client}`].filter(Boolean).join(' · ') || 'Add the court, case number and client under Details, or let the AI fill them in from the files.')}
          ${c.cnr ? ` · <button class="link small" data-cnr>CNR ${esc(c.cnr)}</button>` : ''}
          ${next ? ` · <span class="next-date ${daysUntil(next.date) <= 1 ? 'urgent' : ''}">Next: ${esc(relativeDay(next.date))}${next.time ? ' ' + esc(next.time) : ''} - ${esc(next.title)}</span>` : ''}</div>
        <nav class="tabs" role="tablist">
          ${[['chat', '💬 Chat'], ['files', `📁 Files (${data.documents})`], ['review', '📊 Review'], ['timeline', '🗓 Timeline'], ['details', '⚙ Details']]
            .map(([key, label]) => `<a role="tab" href="#/case/${c.id}/${key}" class="${tab === key ? 'active' : ''}">${label}</a>`).join('')}
          ${tab === 'chat' && data.chats.length > 1 ? `<select class="chat-switch" aria-label="Chats of this case">${data.chats.map((ch) => `<option value="${ch.id}" ${ch.id === current.chatId ? 'selected' : ''}>${esc(ch.title)}</option>`).join('')}</select>` : ''}
          ${tab === 'chat' ? '<button class="link small" data-new-chat title="Start another chat in this case">+ chat</button>' : ''}
        </nav>
      </header>
      <section class="case-body"></section>
    </div>`;
  $('[data-open-folder]', root).onclick = () => api(`/api/cases/${c.id}/open-folder`, { method: 'POST' })
    .then((r) => toast(r.opened ? 'Opened in File Explorer.' : `Folder: ${r.path}`, 'info')).catch(toastError);
  const cnr = $('[data-cnr]', root);
  if (cnr) cnr.onclick = () => cnrHelper(c.cnr);
  const switcher = $('.chat-switch', root);
  if (switcher) switcher.onchange = () => { location.hash = `#/case/${c.id}/chat/${switcher.value}`; };
  const newChat = $('[data-new-chat]', root);
  if (newChat) newChat.onclick = async () => {
    const title = prompt('Name of the new chat in this case', 'Research');
    if (!title) return;
    const chat = await api('/api/chats', { method: 'POST', body: { case_id: c.id, title } });
    location.hash = `#/case/${c.id}/chat/${chat.id}`;
  };
  const body = $('.case-body', root);
  if (tab === 'chat') {
    await renderChat(body, current.chatId);
    window.dispatchEvent(new CustomEvent('chat-ready'));
  } else if (tab === 'files') await renderFiles(body);
  else if (tab === 'review') await renderReviews(body);
  else if (tab === 'timeline') await renderTimeline(body);
  else await renderDetails(body);
}

export function currentCaseId() { return current?.case?.id ?? null; }

// ---------------------------------------------------------------------------
// Files tab: the organised folders (the "data room")
// ---------------------------------------------------------------------------
async function renderFiles(body) {
  const c = current.case;
  const data = await api(`/api/cases/${c.id}/documents`);
  const pending = data.pending_uploads.filter((u) => u.status !== 'filed');
  body.innerHTML = `
    <div class="files-page">
      <div class="toolbar">
        <label class="btn primary">⬆ Add files<input type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp,.tif,.tiff,.bmp"></label>
        <button class="btn" data-zip title="All folders plus an Index.xlsx in one ZIP">⬇ Export data room</button>
        <button class="btn" data-index>⬇ Index (Excel)</button>
        <button class="btn" data-folder>📂 Open folder</button>
        <span class="spacer"></span><input type="search" placeholder="Filter documents…" class="filter" aria-label="Filter documents">
      </div>
      <p class="muted small">Saved on this PC in <code>${esc(data.path)}</code>. The original uploads are kept untouched in <code>_originals</code>.</p>
      ${pending.length ? `<div class="pending">⏳ ${pending.length} uploaded file(s) are not filed yet. Their plan is waiting in the <a href="#/case/${c.id}/chat">chat</a>.</div>` : ''}
      ${data.count ? data.folders.map((f) => `
        <details class="folder" open><summary>📁 ${esc(f.name)} <span class="muted small">(${f.documents.length})</span></summary>
          <table class="docs"><tbody>${f.documents.map((d) => `
            <tr data-doc-row="${d.id}" data-text="${esc((d.title + ' ' + d.doc_type + ' ' + d.filename + ' ' + d.summary).toLowerCase())}">
              <td class="dt">${d.doc_date ? esc(niceDate(d.doc_date, false)) : '<span class="muted">-</span>'}</td>
              <td><button class="link strong" data-open="${d.id}">${esc(d.title)}</button><div class="muted small">${esc(d.filename)} · ${d.pages} p.</div>
                ${d.summary ? `<div class="small">${esc(d.summary)}</div>` : ''}</td>
              <td><span class="badge">${esc(d.doc_type || 'Document')}</span></td>
              <td class="row-actions"><button class="icon-btn small" data-edit="${d.id}" title="Rename / move">✎</button>
                <button class="icon-btn small" data-del="${d.id}" title="Remove">🗑</button></td>
            </tr>`).join('')}</tbody></table></details>`).join('')
        : `<div class="empty-chat"><h3>No documents filed yet</h3><p>Add the case files. The AI reads them, proposes a folder and a new name for each, and files them when you approve.</p></div>`}
    </div>`;
  const all = data.folders.flatMap((f) => f.documents);
  $('input[type=file]', body).onchange = async (e) => {
    const chatId = current.chats[0].id;
    await uploadFiles([...e.target.files], chatId);
    location.hash = `#/case/${c.id}/chat/${chatId}`;
  };
  $('[data-zip]', body).onclick = () => download(`/api/cases/${c.id}/data-room.zip`, 'data-room.zip').catch(toastError);
  $('[data-index]', body).onclick = () => download(`/api/cases/${c.id}/index.xlsx`, 'index.xlsx').catch(toastError);
  $('[data-folder]', body).onclick = () => api(`/api/cases/${c.id}/open-folder`, { method: 'POST' }).catch(toastError);
  $('.filter', body).oninput = (e) => {
    const q = e.target.value.toLowerCase();
    $$('[data-doc-row]', body).forEach((row) => { row.hidden = q && !row.dataset.text.includes(q); });
  };
  $$('[data-open]', body).forEach((b) => { b.onclick = () => showDocument(b.dataset.open); });
  $$('[data-del]', body).forEach((b) => {
    b.onclick = async () => {
      if (!(await confirmBox('Remove this document from the case? The file is moved to workspace\\_deleted.', 'Remove'))) return;
      await api(`/api/documents/${b.dataset.del}`, { method: 'DELETE' }).catch(toastError);
      renderFiles(body);
    };
  });
  $$('[data-edit]', body).forEach((b) => {
    b.onclick = () => {
      const d = all.find((x) => x.id === Number(b.dataset.edit));
      const m = modal('Rename or move', `<form class="form" id="doc-form">
        <label>Title <input name="title" value="${esc(d.title)}"></label>
        <div class="grid2"><label>Type <input name="doc_type" value="${esc(d.doc_type)}"></label>
        <label>Date <input type="date" name="doc_date" value="${esc(d.doc_date)}"></label></div>
        <label>Folder <input name="folder" value="${esc(d.folder)}" list="folder-list"></label>
        <datalist id="folder-list">${data.folders.map((f) => `<option value="${esc(f.name)}">`).join('')}</datalist>
        <label>File name <input name="filename" value="${esc(d.filename)}"></label>
        <label>Summary <textarea name="summary" rows="2">${esc(d.summary)}</textarea></label>
        <div class="actions"><button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary">Save</button></div></form>`);
      $('[data-cancel]', m.root).onclick = m.close;
      $('#doc-form').onsubmit = async (e) => {
        e.preventDefault();
        try {
          await api(`/api/documents/${d.id}`, { method: 'PATCH', body: Object.fromEntries(new FormData(e.target).entries()) });
          m.close(); toast('Saved (the file on disk was renamed too).', 'success'); renderFiles(body);
        } catch (error) { toastError(error); }
      };
    };
  });
}

// ---------------------------------------------------------------------------
// Timeline tab (chronology)
// ---------------------------------------------------------------------------
async function renderTimeline(body) {
  const c = current.case;
  const { items } = await api(`/api/cases/${c.id}/timeline`);
  if (!items.length) {
    body.innerHTML = '<div class="empty-chat"><h3>No dates yet</h3><p>Dated documents and calendar entries of this case appear here in order.</p></div>';
    return;
  }
  const today = todayISO();
  let todayShown = false;
  const rows = [];
  for (const item of items) {
    if (!todayShown && item.date >= today) { rows.push(`<li class="tl-today"><span>Today · ${esc(niceDate(today))}</span></li>`); todayShown = true; }
    const cls = item.kind === 'document' ? 'doc' : `k-${item.kind}`;
    rows.push(`<li class="tl-item ${cls} ${item.date < today ? 'past' : ''} ${item.status === 'done' ? 'done' : ''}">
      <span class="tl-date">${esc(niceDate(item.date, false))}${item.time ? ' ' + esc(item.time) : ''}</span>
      <span class="tl-what">${item.kind === 'document'
        ? `<button class="link" data-open="${item.document_id}">📄 ${esc(item.title)}</button> <span class="badge">${esc(item.type || 'Document')}</span>${item.summary ? `<div class="small muted">${esc(item.summary)}</div>` : ''}`
        : `<button class="link" data-event="${item.event_id}">${item.kind === 'hearing' ? '⚖' : '⏰'} ${esc(item.title)}</button> <span class="badge">${esc(KIND_LABEL[item.kind] || item.kind)}</span>`}</span></li>`);
  }
  if (!todayShown) rows.push(`<li class="tl-today"><span>Today · ${esc(niceDate(today))}</span></li>`);
  body.innerHTML = `<div class="timeline-page"><div class="toolbar"><h3>Chronology</h3><span class="spacer"></span>
    <button class="btn small" data-add-event>+ Add date</button>
    <button class="btn small" data-chronology>⚡ Write the list of dates (AI)</button></div><ol class="timeline">${rows.join('')}</ol></div>`;
  $$('[data-open]', body).forEach((b) => { b.onclick = () => showDocument(b.dataset.open); });
  $$('[data-event]', body).forEach((b) => {
    b.onclick = async () => {
      const events = await api(`/api/events?start=1900-01-01&end=2999-12-31&case_id=${c.id}`);
      const found = events.find((e) => e.id === Number(b.dataset.event));
      if (found) eventDialog(found);
    };
  });
  $('[data-add-event]', body).onclick = () => eventDialog({ case_id: c.id, date: today });
  $('[data-chronology]', body).onclick = () => {
    window.addEventListener('chat-ready', () => import('./chat.js').then((m) => m.workflowPicker('case-chronology')), { once: true });
    location.hash = `#/case/${c.id}/chat`;
  };
}

// ---------------------------------------------------------------------------
// Details tab (and the eCourts CNR helper)
// ---------------------------------------------------------------------------
async function renderDetails(body) {
  const c = current.case;
  const field = (name, label, placeholder = '') => `<label>${label}<input name="${name}" value="${esc(c[name] || '')}" placeholder="${esc(placeholder)}"></label>`;
  body.innerHTML = `<form class="form details" id="case-form">
    ${field('title', 'Case title')}
    <div class="grid2">
      ${field('client', 'Client')}${field('opponent', 'Other side')}
      ${field('side', 'We act for', 'Petitioner / Respondent / Accused / Complainant')}${field('court', 'Court')}
      ${field('case_type', 'Type', 'Criminal / Civil / Family / Writ …')}${field('case_number', 'Case number')}
      ${field('stage', 'Stage', 'e.g. Evidence, Arguments, Bail hearing')}
      <label>CNR (eCourts)<span class="cnr-row"><input name="cnr" value="${esc(c.cnr || '')}" maxlength="20" placeholder="e.g. DLST010012342026"><button type="button" class="btn small" data-cnr>Check</button></span></label>
    </div>
    <label>Notes (the AI reads these too)<textarea name="notes" rows="4">${esc(c.notes || '')}</textarea></label>
    <div class="actions">
      <button type="button" class="btn danger" data-delete>Delete case</button>
      <button type="button" class="btn" data-status>${c.status === 'closed' ? 'Reopen case' : 'Close case'}</button>
      <span class="spacer"></span><button class="btn primary">Save</button></div>
  </form>`;
  $('#case-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api(`/api/cases/${c.id}`, { method: 'PATCH', body: Object.fromEntries(new FormData(e.target).entries()) });
      toast('Case details saved.', 'success');
      await loadSidebar();
      renderCase($('#view'), c.id, 'details');
    } catch (error) { toastError(error); }
  };
  $('[data-cnr]', body).onclick = () => cnrHelper($('[name=cnr]', body).value);
  $('[data-status]', body).onclick = async () => {
    await api(`/api/cases/${c.id}`, { method: 'PATCH', body: { status: c.status === 'closed' ? 'active' : 'closed' } }).catch(toastError);
    await loadSidebar(); renderCase($('#view'), c.id, 'details');
  };
  $('[data-delete]', body).onclick = async () => {
    if (!(await confirmBox(`Delete “${c.title}”? Its chats and calendar entries are removed; its folder is moved to workspace\\_deleted.`, 'Delete case'))) return;
    await api(`/api/cases/${c.id}`, { method: 'DELETE' }).catch(toastError);
    await loadSidebar(); location.hash = '#/';
  };
}

export async function cnrHelper(cnr) {
  if (!cnr) { toast('Type the 16-character CNR first.', 'warn'); return; }
  const info = await api(`/api/cnr/${encodeURIComponent(cnr)}`);
  modal('eCourts case status', info.valid ? `
    <p><b>${esc(info.cnr)}</b> looks valid.</p>
    <table class="kv"><tr><td>State</td><td>${esc(info.state)} (${esc(info.state_code)})</td></tr>
      <tr><td>District / establishment code</td><td>${esc(info.district_code)} / ${esc(info.establishment)}</td></tr>
      <tr><td>Case serial</td><td>${esc(info.case_serial)}</td></tr><tr><td>Year</td><td>${esc(info.year)}${info.year_ok ? '' : ' ⚠ unusual'}</td></tr></table>
    <p>eCourts has no public API, so the status is checked on the official site. You type the CAPTCHA yourself, and the app never bypasses it.</p>
    <div class="actions left">
      <button class="btn" data-copy>Copy CNR</button>
      <a class="btn primary" href="${esc(info.ecourts_url)}" target="_blank" rel="noopener">District courts ↗</a>
      <a class="btn" href="${esc(info.high_court_url)}" target="_blank" rel="noopener">High Courts ↗</a></div>
    <p class="muted small">Or send an SMS: <code>${esc(info.sms)}</code> to ${esc(info.sms_number)}.<br>
      Tip: save the eCourts case-status page as a PDF (Ctrl+P → Save as PDF) and drop it into this case. Its next hearing date will be proposed for your calendar.</p>`
    : `<p class="warn">${esc(info.problem)}</p>`);
  const copy = $('.modal [data-copy]');
  if (copy) copy.onclick = () => { navigator.clipboard.writeText(info.cnr); toast('CNR copied - paste it on the eCourts page.', 'success'); };
}

// ---------------------------------------------------------------------------
// Review tab: review tables, due diligence and the risk chart
// ---------------------------------------------------------------------------
async function renderReviews(body) {
  const c = current.case;
  if (current.reviewId) return renderReview(body, current.reviewId);
  const reviews = await api(`/api/cases/${c.id}/reviews`);
  body.innerHTML = `<div class="reviews-page">
    <div class="toolbar"><h3>Review tables & due diligence</h3><span class="spacer"></span><button class="btn primary" data-new>+ New review</button></div>
    <p class="muted">Ask the same questions of every document. One AI reader per document works in parallel and fills a table with answers, quotes, pages and risk ratings. A final report and a risk chart follow.</p>
    ${reviews.length ? `<ul class="review-list">${reviews.map((r) => `<li><a href="#/case/${c.id}/review/${r.id}"><b>${esc(r.title)}</b></a>
      <span class="badge ${r.status === 'done' ? 'good' : r.status === 'failed' ? 'bad' : 'info'}">${esc(r.status)}</span>
      <span class="muted small">${esc(niceDate(r.created_at.slice(0, 10), false))}</span></li>`).join('')}</ul>` : '<p class="muted">No reviews yet.</p>'}
  </div>`;
  $('[data-new]', body).onclick = () => newReviewDialog(c);
}

async function newReviewDialog(c) {
  const [templates, docs] = await Promise.all([api('/api/review-templates'), api(`/api/cases/${c.id}/documents`)]);
  const documents = docs.folders.flatMap((f) => f.documents);
  if (!documents.length) { toast('File some documents in this case first.', 'warn'); return; }
  const m = modal('New review', `<form class="form" id="review-form">
    <label>Template <select name="template">${templates.map((t) => `<option value="${t.id}">${esc(t.title)}</option>`).join('')}</select></label>
    <label>Title <input name="title" placeholder="(optional)"></label>
    <div class="columns-preview"></div>
    <fieldset><legend>Documents</legend><div class="doc-picker">${documents.map((d) => `<label class="doc-pick"><input type="checkbox" name="doc" value="${d.id}" checked> ${esc(d.title)} <span class="muted small">${esc(d.doc_type)}</span></label>`).join('')}</div></fieldset>
    <p class="muted small">This uses one AI call per document (in parallel), plus one for the report.</p>
    <div class="actions"><button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary">Start review</button></div></form>`, { wide: true });
  $('[data-cancel]', m.root).onclick = m.close;
  const preview = $('.columns-preview', m.root);
  const custom = [{ label: '', question: '' }];
  const drawColumns = () => {
    const template = templates.find((t) => t.id === $('[name=template]', m.root).value);
    if (template.id === 'custom') {
      preview.innerHTML = `<p><b>Your columns</b> (a short label and the question to ask each document)</p>${custom.map((col, i) => `
        <div class="col-row"><input placeholder="Label, e.g. Termination" data-l="${i}" value="${esc(col.label)}">
        <input placeholder="Question, e.g. How can each party terminate?" data-q="${i}" value="${esc(col.question)}"></div>`).join('')}
        <button type="button" class="link small" data-add>+ Add column</button>`;
      $$('[data-l]', preview).forEach((input) => { input.oninput = () => { custom[input.dataset.l].label = input.value; }; });
      $$('[data-q]', preview).forEach((input) => { input.oninput = () => { custom[input.dataset.q].question = input.value; }; });
      $('[data-add]', preview).onclick = () => { custom.push({ label: '', question: '' }); drawColumns(); };
    } else {
      preview.innerHTML = `<p class="small"><b>Columns:</b> ${template.columns.map((col) => `${esc(col.label)}${col.risk ? ' ⚑' : ''}`).join(' · ')}</p>
        <p class="muted small">⚑ = rated for risk (high / medium / low)</p>`;
    }
  };
  $('[name=template]', m.root).onchange = drawColumns;
  drawColumns();
  $('#review-form').onsubmit = async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const body = { template: form.get('template'), title: form.get('title'), document_ids: form.getAll('doc').map(Number) };
    if (body.template === 'custom') body.columns = custom.filter((col) => col.label.trim() || col.question.trim()).map((col) => ({ ...col, risk: true }));
    try {
      const created = await api(`/api/cases/${c.id}/reviews`, { method: 'POST', body });
      m.close();
      location.hash = `#/case/${c.id}/review/${created.id}`;
    } catch (error) { toastError(error); }
  };
}

const RISK_ORDER = ['high', 'medium', 'low'];
const RISK_LABEL = { high: 'High', medium: 'Medium', low: 'Low' };

function riskChart(review) {
  // Horizontal stacked bars: one per risk column, split into high / medium / low.
  const rows = review.columns.filter((col) => col.risk).map((col) => ({ label: col.label, ...(review.risk.by_column[col.key] || {}) }));
  if (!rows.length) return '';
  const total = Math.max(1, ...rows.map((r) => RISK_ORDER.reduce((s, k) => s + (r[k] || 0), 0)));
  const width = 320;
  const bars = rows.map((r, i) => {
    let x = 0;
    const segments = RISK_ORDER.map((k) => {
      const w = ((r[k] || 0) / total) * width;
      const seg = w ? `<rect x="${150 + x}" y="${i * 30 + 6}" width="${w}" height="18" rx="3" class="r-${k}"><title>${esc(r.label)}: ${r[k]} ${k}</title></rect>
        ${w > 18 ? `<text x="${150 + x + w / 2}" y="${i * 30 + 19}" class="seg-label">${r[k]}</text>` : ''}` : '';
      x += w;
      return seg;
    }).join('');
    return `<text x="140" y="${i * 30 + 19}" class="row-label" text-anchor="end">${esc(r.label.slice(0, 22))}</text>${segments}`;
  }).join('');
  return `<figure class="risk-chart"><figcaption>Risk by question</figcaption>
    <svg viewBox="0 0 ${150 + width + 10} ${rows.length * 30 + 8}" role="img" aria-label="Risk chart">${bars}</svg>
    <div class="legend">${RISK_ORDER.map((k) => `<span><i class="r-${k}"></i>${RISK_LABEL[k]}</span>`).join('')}</div></figure>`;
}

async function renderReview(body, reviewId) {
  const c = current.case;
  const review = await api(`/api/reviews/${reviewId}`);
  const cells = new Map(review.cells.map((cell) => [`${cell.document_id}|${cell.column_key}`, cell]));
  const done = review.cells.filter((cell) => cell.status === 'done' || cell.status === 'error').length;
  body.innerHTML = `<div class="review-page" data-review="${review.id}">
    <div class="toolbar"><a href="#/case/${c.id}/review" class="link">‹ All reviews</a><h3>${esc(review.title)}</h3>
      <span class="badge ${review.status === 'done' ? 'good' : review.status === 'failed' ? 'bad' : 'info'}">${esc(review.status)}</span>
      ${review.status === 'running' ? `<span class="muted small">${spinner()} ${done} of ${review.cells.length} cells</span>` : ''}
      <span class="spacer"></span>
      <button class="btn small" data-xlsx>⬇ Excel</button><button class="btn small" data-rerun>↻ Run again</button><button class="btn small danger" data-del>Delete</button></div>
    ${riskChart(review)}
    <div class="table-wrap review-table"><table><thead><tr><th>Document</th>${review.columns.map((col) => `<th title="${esc(col.question)}">${esc(col.label)}</th>`).join('')}</tr></thead>
    <tbody>${review.documents.map((d) => `<tr><th><button class="link" data-open="${d.id}">${esc(d.title)}</button><div class="muted small">${esc(d.doc_type)} ${d.doc_date ? '· ' + esc(niceDate(d.doc_date, false)) : ''}</div></th>
      ${review.columns.map((col) => {
        const cell = cells.get(`${d.id}|${col.key}`) || {};
        const busy = cell.status === 'running' || cell.status === 'pending';
        return `<td class="cell risk-${cell.risk || 'none'} ${cell.status === 'error' ? 'err' : ''}">${busy ? spinner() : esc(cell.answer || '')}
          ${cell.page ? ` <button class="chip doc verified" data-doc="${d.id}" data-page="${cell.page}">p.${cell.page}</button>` : ''}
          ${cell.quote ? `<div class="cell-quote">“${esc(cell.quote)}”</div>` : ''}</td>`;
      }).join('')}</tr>`).join('')}</tbody></table></div>
    ${review.report ? `<section class="report"><h3>Due-diligence report</h3>${markdown(review.report, null, true)}</section>`
      : review.status === 'running' ? `<p class="muted">${spinner()} The report is written when every document is done.</p>` : ''}
  </div>`;
  $$('[data-open]', body).forEach((b) => { b.onclick = () => showDocument(b.dataset.open); });
  $('[data-xlsx]', body).onclick = () => download(`/api/reviews/${review.id}/xlsx`, 'review.xlsx').catch(toastError);
  $('[data-rerun]', body).onclick = async () => { await api(`/api/reviews/${review.id}/rerun`, { method: 'POST' }).catch(toastError); renderReview(body, review.id); };
  $('[data-del]', body).onclick = async () => {
    if (!(await confirmBox('Delete this review?', 'Delete'))) return;
    await api(`/api/reviews/${review.id}`, { method: 'DELETE' }).catch(toastError);
    location.hash = `#/case/${c.id}/review`;
  };
}

let reviewTimer = null;
export function onReview(data) {
  const page = $(`.review-page[data-review="${data.review_id}"]`);
  if (!page || !current) return;
  clearTimeout(reviewTimer);
  reviewTimer = setTimeout(() => renderReview(page.parentElement, data.review_id), 400);
}

export function refreshFilesIfOpen() {
  if (current && current.tab === 'files') renderFiles($('.case-body'));
  if (current && current.tab === 'timeline') renderTimeline($('.case-body'));
}
