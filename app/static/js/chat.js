// chat.js - a chat (in a case or general): messages, streamed answers, file uploads,
// the AI's organisation plan (edit / approve) and tasks (click -> small form -> Start -> result;
// in the code a task is a "workflow" / "skill").

import {
  $, $$, KIND_LABEL, S, api, confirmBox, download, esc, h, markdown, modal, niceDate, spinner, streamPost,
  toast, toastError,
} from './core.js';

let view = null;          // {root, chat, case, messages}
let controller = null;    // to stop a streaming answer
let deep = false;         // the "Deep" button: the strongest model, thinking longer (stays on until clicked off)
const planEdits = {};     // plan id -> the plan as you are editing it
const FOLDERS = ['01 Pleadings & Petitions', '02 Applications', '03 Orders & Judgments', '04 Police Records',
  '05 Evidence & Exhibits', '06 Notices & Correspondence', '07 Client Documents', '08 Drafts', '09 Research & Notes', '99 Other'];
const FIELD_LABELS = { title: 'Case title', client: 'Client', opponent: 'Other side', side: 'We act for', court: 'Court',
  case_type: 'Type', case_number: 'Case number', cnr: 'CNR', stage: 'Stage' };

export function currentChatId() { return view?.chat?.id ?? null; }

export async function renderChat(root, chatId) {
  const page = await api(`/api/chats/${chatId}`);
  view = { root, chat: page.chat, case: page.case, messages: page.messages, documents: page.documents };
  S.chatPage = page;
  const isCase = Boolean(page.case);
  root.innerHTML = `
    <div class="chat" id="chat-drop">
      ${!isCase ? `<div class="chat-head"><h2 class="chat-title" title="Click to rename">${esc(page.chat.title)}</h2>
        <div class="chat-head-actions">
          ${page.documents.length ? `<button class="btn small" data-make-case>Make a case from these files</button>` : ''}
          <button class="icon-btn" data-delete-chat title="Delete chat" aria-label="Delete chat">🗑</button></div></div>` : ''}
      <div class="messages" aria-live="polite"></div>
      <div class="drop-hint" hidden>Drop files to add them ${isCase ? 'to this case' : 'to this chat'}</div>
      ${composerHtml(isCase)}
    </div>`;
  renderMessages();
  bindComposer(root);
  if (!isCase) {
    const title = $('.chat-title', root);
    title.onclick = async () => {
      const name = prompt('Rename this chat', page.chat.title);
      if (name && name.trim()) { await api(`/api/chats/${chatId}`, { method: 'PATCH', body: { title: name.trim() } }); title.textContent = name.trim(); }
    };
    $('[data-delete-chat]', root).onclick = async () => {
      if (!(await confirmBox('Delete this chat and its files?', 'Delete'))) return;
      await api(`/api/chats/${chatId}`, { method: 'DELETE' }).catch(toastError);
      location.hash = '#/';
    };
    const make = $('[data-make-case]', root);
    if (make) make.onclick = () => makeCaseFromChat(chatId);
  }
}

function composerHtml(isCase) {
  const style = S.settings?.style || 'detailed';
  return `<form class="composer" autocomplete="off">
    <textarea rows="1" maxlength="8000" placeholder="${isCase ? 'Ask about this case, or drop its files here…' : 'Ask anything about Indian law, or drop a file…'}" aria-label="Your question"></textarea>
    <div class="composer-bar">
      <label class="icon-btn" title="Add files (PDF, Word, photos)">📎<input type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp,.tif,.tiff,.bmp"></label>
      <button type="button" class="btn small ghost" data-workflows title="Ready-made tasks: check an FIR, draft a bail application, reply to a notice…">📋 Tasks</button>
      <button type="button" class="btn small ghost deep-btn" data-deep aria-pressed="${deep}"
        title="Deep answer: the strongest model thinks longer. Use it for hard questions - slower, and about twice the cost.">🧠 Deep</button>
      <div class="seg small" role="group" aria-label="Answer style">
        <button type="button" data-style="detailed" class="${style === 'detailed' ? 'active' : ''}">For lawyers</button>
        <button type="button" data-style="simple" class="${style === 'simple' ? 'active' : ''}">Plain words</button></div>
      <span class="spacer"></span>
      <button type="button" class="btn small" data-stop hidden>■ Stop</button>
      <button class="btn primary send" aria-label="Send">Send ➤</button>
    </div></form>`;
}

function bindComposer(root) {
  const form = $('.composer', root);
  const box = $('textarea', form);
  const grow = () => { box.style.height = 'auto'; box.style.height = Math.min(220, box.scrollHeight) + 'px'; };
  box.oninput = grow;
  box.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } };
  form.onsubmit = (e) => {
    e.preventDefault();
    const question = box.value.trim();
    if (!question || controller) return;
    box.value = ''; grow();
    ask(question);
  };
  $$('[data-style]', form).forEach((b) => {
    b.onclick = () => { $$('[data-style]', form).forEach((x) => x.classList.toggle('active', x === b)); };
  });
  $('input[type=file]', form).onchange = (e) => { uploadFiles([...e.target.files]); e.target.value = ''; };
  $('[data-workflows]', form).onclick = () => workflowPicker();
  const deepButton = $('[data-deep]', form);
  deepButton.onclick = () => {
    deep = !deep;
    deepButton.setAttribute('aria-pressed', String(deep));
    if (deep) toast('Deep answers on: the strongest model, thinking longer. Click 🧠 Deep again to turn it off.', 'info', 4000);
  };
  $('[data-stop]', form).onclick = () => { if (controller) controller.abort(); };
  const zone = $('#chat-drop', root);
  let depth = 0;
  zone.addEventListener('dragenter', (e) => { if (e.dataTransfer.types.includes('Files')) { depth += 1; $('.drop-hint', zone).hidden = false; } });
  zone.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) $('.drop-hint', zone).hidden = true; });
  zone.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); depth = 0; $('.drop-hint', zone).hidden = true;
    if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]);
  });
}

export function setComposerText(text, send = false) {
  const box = view && $('.composer textarea', view.root);
  if (!box) return;
  box.value = text;
  box.focus();
  if (send) $('.composer', view.root).requestSubmit();
}

// ---------------------------------------------------------------------------
// Rendering messages
// ---------------------------------------------------------------------------
function renderMessages() {
  const list = $('.messages', view.root);
  if (!view.messages.length) {
    list.innerHTML = emptyState();
    $$('[data-example]', list).forEach((b) => { b.onclick = () => setComposerText(b.textContent, true); });
    fillTaskButtons(list);
    return;
  }
  list.innerHTML = '';
  for (const message of view.messages) list.append(messageElement(message));
  list.scrollTop = list.scrollHeight;
}

const TASKS_HTML = `<div class="task-strip"><h4>Start a task</h4><div class="task-buttons" data-tasks>${spinner()}</div></div>`;

function emptyState() {
  if (view.case) {
    return `<div class="empty-chat"><h3>${esc(view.case.title)}</h3>
      <p><b>Drop the case files here</b> (FIR, orders, petitions, notices, scans, photos). I'll read them and propose how to file and name them. I'll also suggest the hearing dates and deadlines for your calendar, and nothing moves until you approve.</p>
      ${TASKS_HTML}
      <p class="muted">Or ask anything, for example:</p>
      <div class="examples"><button data-example>What did the court direct at the last hearing?</button>
      <button data-example>Make a list of dates from the file</button><button data-example>What are our strongest grounds for bail?</button></div></div>`;
  }
  return `<div class="empty-chat"><h3>Ask anything about Indian law</h3>
    <p class="muted">Answers cite the BNS, BNSS, BSA, IPC, CrPC, CPC, the Constitution, 160+ other Acts and court rule books, and every citation is checked. Questions work in English, हिंदी, Hinglish, தமிழ், తెలుగు, বাংলা, मराठी and more.</p>
    ${TASKS_HTML}
    <p class="muted">Or ask a question, for example:</p>
    <div class="examples"><button data-example>What is the punishment for cheating under the BNS, and what was it under the IPC?</button>
    <button data-example>When is anticipatory bail available under the BNSS?</button>
    <button data-example>Cheque bounce: what are the time limits under section 138 NI Act?</button>
    <button data-example>Limitation period for a suit for recovery of money?</button>
    <button data-example>पुलिस गिरफ्तारी के बाद 24 घंटे में क्या करना होता है?</button></div></div>`;
}

function messageElement(m) {
  if (m.role === 'user') {
    return h(`<div class="msg user" data-id="${m.id}"><div class="bubble">${esc(m.content).replace(/\n/g, '<br>')}</div></div>`);
  }
  if (m.role === 'assistant') return answerElement(m);
  if (m.role === 'files') return filesElement(m);
  if (m.role === 'plan') return planElement(m);
  if (m.role === 'workflow') return workflowElement(m);
  return noticeElement(m);
}

function noticeElement(m) {
  const data = m.data || {};
  const docs = (data.documents || []).map((d) => `<li><button class="chip doc verified" data-doc="${d.id}">📄 ${esc(d.title)}</button>
    ${d.folder ? `<span class="muted small">📁 ${esc(d.folder)}</span>` : ''}</li>`).join('');
  const events = (data.events || []).map((e) => `<li>📅 ${esc(niceDate(e.date))}${e.time ? ' ' + esc(e.time) : ''}: ${esc(e.title)}</li>`).join('');
  const caseLink = data.case_id ? ` <a href="#/case/${data.case_id}">Open the case →</a>` : '';
  return h(`<div class="msg notice" data-id="${m.id}"><div class="notice-box">✓ ${esc(m.content)}${caseLink}
    ${docs || events ? `<ul class="plain">${docs}${events}</ul>` : ''}</div></div>`);
}

function filesElement(m) {
  const data = m.data || {};
  const uploads = m.uploads || [];
  const el = h(`<div class="msg files" data-id="${m.id}" data-batch="${esc(data.batch || '')}"><div class="files-box">
    <div class="files-head">📎 ${uploads.length} file${uploads.length === 1 ? '' : 's'} added</div>
    <ul class="uploads">${uploads.map(uploadRow).join('')}</ul>
    ${(data.skipped || []).map((s) => `<p class="warn small">Skipped ${esc(s.name)}: ${esc(s.reason)}</p>`).join('')}
    ${(data.duplicates || []).length ? `<p class="muted small">Already in this case before: ${data.duplicates.map(esc).join(', ')}</p>` : ''}
    ${data.can_make_case ? '<button class="btn small" data-make-case>Make a case from these files</button>' : ''}
    </div></div>`);
  const make = $('[data-make-case]', el);
  if (make) make.onclick = () => makeCaseFromChat(view.chat.id);
  return el;
}

function uploadRow(u) {
  const icon = { received: '⏳', reading: spinner(), ready: '✓', filed: '✓', failed: '⚠' }[u.status] || '';
  return `<li data-upload="${u.id}" class="up-${u.status}"><span class="up-icon">${icon}</span>
    <span class="up-name">${esc(u.original_name)}</span>
    <span class="up-progress muted small">${esc(u.status === 'failed' ? (u.error || 'Could not read') : (u.progress || u.status))}</span></li>`;
}

function answerElement(m) {
  const data = m.data || {};
  const el = h(`<div class="msg assistant" data-id="${m.id}"><div class="answer">
    <div class="answer-body"></div>
    <div class="answer-foot"></div></div></div>`);
  fillAnswer(el, m.content, data);
  return el;
}

function sourcesHtml(sources) {
  if (!sources) return '';
  const docs = (sources.documents || []).map((d) => `<button class="chip doc verified" data-doc="${d.document_id}" data-page="${d.page}" title="${esc(d.snippet)}">📄 ${esc(d.title)} · p.${d.page}</button>`);
  const acts = (sources.acts || []).map((a) => `<button class="chip verified section" data-cite-id="${esc(a.id)}" data-cite-kind="section" title="${esc(a.title)}">${esc(a.label)}</button>`);
  const cons = (sources.constitution || []).map((a) => `<button class="chip verified constitution" data-cite-id="${esc(a.id)}" data-cite-kind="constitution" title="${esc(a.title)}">${esc(a.label)}</button>`);
  const cases = (sources.judgments || []).map((c) => `<button class="chip verified judgment" data-cite-id="${esc(c.id)}" data-cite-kind="judgment">${esc(c.label || c.short || c.name)}</button>`);
  const all = [...docs, ...acts, ...cons, ...cases];
  if (!all.length) return '';
  const translated = sources.translation && sources.translation !== 'none'
    ? `<p class="muted small">Searched in English as: “${esc(sources.query)}”</p>` : '';
  return `<details class="sources"><summary>Sources searched (${all.length})</summary>${translated}<div class="chips">${all.join(' ')}</div></details>`;
}

function fillAnswer(el, content, data, streaming = false) {
  const body = $('.answer-body', el);
  if (data.mode === 'search_only') {
    body.innerHTML = `<p class="muted">AI answers are off (search-only mode). These are the most relevant sources. Click one to read it.</p>`;
  } else {
    body.innerHTML = markdown(content, data.citations, streaming) || (streaming ? spinner('Thinking…') : '');
  }
  if (data.error) body.insertAdjacentHTML('beforeend', `<p class="warn">${esc(data.error)}</p>`);
  const foot = $('.answer-foot', el);
  if (streaming) { foot.innerHTML = sourcesHtml(data.sources); return; }
  const counts = data.citations?.counts;
  const check = counts ? `<span class="check ${counts.unknown || counts.outside ? 'bad' : 'good'}" title="Green = from the sources; amber = in the library, not given to the AI; red = not found">
    ${counts.verified ? `🟢 ${counts.verified}` : ''} ${counts.library ? `🟠 ${counts.library}` : ''} ${(counts.unknown || 0) + (counts.outside || 0) ? `🔴 ${(counts.unknown || 0) + (counts.outside || 0)}` : ''}</span>` : '';
  const cost = data.cost ? `<span class="muted small" title="${data.cost.billing === 'subscription' ? 'Included in your Claude subscription; this is what the API would have cost' : 'Charged to your API credits'}${data.cost.cached_tokens ? ` (${data.cost.cached_tokens.toLocaleString('en-IN')} tokens read from the cache at a lower price)` : ''}">${data.deep ? '🧠 Deep · ' : ''}${esc(data.model || '')} · ≈ ₹${data.cost.inr}</span>` : '';
  foot.innerHTML = `${sourcesHtml(data.sources)}
    <div class="answer-actions">${check}
      ${content ? `<button class="link small" data-copy>Copy</button>
      <button class="link small" data-word>Word</button>
      ${view?.case ? '<button class="link small" data-save>Save to case (08 Drafts)</button>' : ''}` : ''}
      <span class="spacer"></span>${cost}</div>
    <p class="disclaimer">Legal information, not a substitute for your judgment. Check every citation before relying on it.</p>`;
  const id = el.dataset.id;
  const copy = $('[data-copy]', foot);
  if (copy) copy.onclick = () => { navigator.clipboard.writeText(content); toast('Copied.', 'success', 2000); };
  const word = $('[data-word]', foot);
  if (word) word.onclick = () => download(`/api/messages/${id}/docx`, 'answer.docx').catch(toastError);
  const save = $('[data-save]', foot);
  if (save) {
    save.onclick = async () => {
      const title = prompt('Title of the draft', data.title || 'Research note');
      if (title === null) return;
      try {
        const saved = await download(`/api/messages/${id}/docx?save=1&title=${encodeURIComponent(title)}`, 'draft.docx');
        toast(saved ? `Saved in the case folder: ${saved}` : 'Downloaded.', 'success');
      } catch (error) { toastError(error); }
    };
  }
}

// ---------------------------------------------------------------------------
// Asking (streamed)
// ---------------------------------------------------------------------------
async function ask(question) {
  const list = $('.messages', view.root);
  if ($('.empty-chat', list)) list.innerHTML = '';
  const style = $('.composer [data-style].active', view.root)?.dataset.style || 'detailed';
  const askDeep = deep;
  const userEl = h(`<div class="msg user"><div class="bubble">${esc(question).replace(/\n/g, '<br>')}</div></div>`);
  const answerEl = h(`<div class="msg assistant streaming"><div class="answer"><div class="answer-body">${spinner('Searching the case file and the law…')}</div><div class="answer-foot"></div></div></div>`);
  list.append(userEl, answerEl);
  list.scrollTop = list.scrollHeight;
  controller = new AbortController();
  $('[data-stop]', view.root).hidden = false;
  $('.send', view.root).disabled = true;
  let text = '';
  let sources = null;
  let lastPaint = 0;
  const chatId = view.chat.id;
  try {
    await streamPost(`/api/chats/${chatId}/ask`, { question, style, deep: askDeep }, {
      start: (data) => { userEl.dataset.id = data.user_message.id; if (data.chat && !view.case) { $('.chat-title', view.root) && ($('.chat-title', view.root).textContent = data.chat.title); } },
      sources: (data) => {
        sources = data; fillAnswer(answerEl, '', { sources }, true);
        $('.answer-body', answerEl).innerHTML = spinner(!data.ai_enabled ? '' : askDeep ? 'Thinking deeply… (a Deep answer takes longer)' : 'Writing the answer…');
      },
      delta: (data) => {
        text += data.text;
        const now = performance.now();
        if (now - lastPaint > 80) { lastPaint = now; fillAnswer(answerEl, text, { sources }, true); list.scrollTop = list.scrollHeight; }
      },
      done: (data) => {
        answerEl.classList.remove('streaming');
        answerEl.dataset.id = data.message.id;
        view.messages.push({ id: Number(userEl.dataset.id), role: 'user', content: question, data: {} }, data.message);
        fillAnswer(answerEl, data.message.content, data.message.data || { sources, mode: data.mode });
      },
      error: (data) => {
        answerEl.classList.remove('streaming');
        fillAnswer(answerEl, text, { sources, error: data.message });
      },
    }, controller.signal);
  } catch (error) {
    answerEl.classList.remove('streaming');
    if (error.name === 'AbortError') fillAnswer(answerEl, text + '\n\n*(stopped)*', { sources });
    else fillAnswer(answerEl, text, { sources, error: error.message });
  } finally {
    controller = null;
    if (view && view.chat.id === chatId) {
      $('[data-stop]', view.root).hidden = true;
      $('.send', view.root).disabled = false;
    }
    list.scrollTop = list.scrollHeight;
    window.dispatchEvent(new CustomEvent('chat-updated'));
  }
}

// ---------------------------------------------------------------------------
// Uploading files
// ---------------------------------------------------------------------------
export async function uploadFiles(fileList, targetChatId = null) {
  const chatId = targetChatId || view?.chat?.id;
  if (!chatId || !fileList.length) return;
  const max = (S.status?.max_upload_mb || 60) * 1024 * 1024;
  const big = fileList.filter((f) => f.size > max);
  if (big.length) toast(`Too large (over ${S.status?.max_upload_mb || 60} MB): ${big.map((f) => f.name).join(', ')}`, 'warn');
  const form = new FormData();
  fileList.filter((f) => f.size <= max).forEach((f) => form.append('uploads', f, f.name));
  if (![...form.keys()].length) return;
  toast(`Uploading ${fileList.length} file${fileList.length === 1 ? '' : 's'}…`, 'info', 2500);
  try {
    const response = await fetch(`/api/chats/${chatId}/uploads`, { method: 'POST', body: form });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Upload failed');
    const result = await response.json();
    if (view && view.chat.id === chatId) addOrReplaceMessage(result.message);
    for (const s of result.skipped || []) toast(`${s.name}: ${s.reason}`, 'warn', 7000);
  } catch (error) { toastError(error); }
}

function addOrReplaceMessage(message) {
  if (!view) return;
  const list = $('.messages', view.root);
  if ($('.empty-chat', list)) list.innerHTML = '';
  const existing = $(`.msg[data-id="${message.id}"]`, list);
  if (existing?.classList.contains('running')) return; // a task is writing its result here right now
  const el = messageElement(message);
  if (existing) existing.replaceWith(el);
  else {
    list.append(el);
    view.messages.push(message);
    list.scrollTop = list.scrollHeight;
  }
}

async function makeCaseFromChat(chatId) {
  const title = prompt('Name of the new case (you can change it later)', '');
  if (title === null) return;
  try {
    const result = await api(`/api/chats/${chatId}/make-case`, { method: 'POST', body: { title } });
    toast('Case created. Its filing plan is being prepared.', 'success');
    location.hash = `#/case/${result.case.id}/chat/${result.chats[0].id}`;
  } catch (error) { toastError(error); }
}

// Live events from the server
export function onUpload(data) {
  if (!view || data.chat_id !== view.chat.id) return;
  const row = $(`[data-upload="${data.upload.id}"]`, view.root);
  if (row) row.outerHTML = uploadRow(data.upload);
}
export async function onMessage(data) {
  if (!view || data.chat_id !== view.chat.id) return;
  let message = data.message;
  if (message.role === 'plan' && message.data?.plan_id && !message.plan) message.plan = await api(`/api/plans/${message.data.plan_id}`).catch(() => null);
  if (message.role === 'files' && message.data?.batch && !message.uploads) {
    const page = await api(`/api/chats/${view.chat.id}`).catch(() => null);
    message = page?.messages.find((m) => m.id === message.id) || message;
  }
  if (message.role === 'workflow' && message.data?.run_id && !message.run) message.run = await api(`/api/runs/${message.data.run_id}`).catch(() => null);
  addOrReplaceMessage(message);
}
export async function onPlan(data) {
  if (!view || data.chat_id !== view.chat.id) return;
  const el = $(`.msg.plan[data-plan="${data.plan_id}"]`, view.root);
  if (!el) return;
  const plan = await api(`/api/plans/${data.plan_id}`).catch(() => null);
  if (!plan) return;
  const message = view.messages.find((m) => m.data?.plan_id === data.plan_id && m.role === 'plan') || { id: el.dataset.id, role: 'plan', data: { plan_id: data.plan_id } };
  message.plan = plan;
  if (plan.status !== 'draft') delete planEdits[plan.id];
  el.replaceWith(planElement(message));
}
export async function onRun(data) {
  if (!view || data.chat_id !== view.chat.id) return;
  const el = $(`.msg.workflow[data-run="${data.run_id}"]`, view.root);
  if (!el || el.classList.contains('running')) return;
  const run = await api(`/api/runs/${data.run_id}`).catch(() => null);
  if (!run) return;
  const message = view.messages.find((m) => m.data?.run_id === data.run_id) || { id: el.dataset.id, role: 'workflow', data: { run_id: data.run_id } };
  if (run.status === 'done') {
    const page = await api(`/api/chats/${view.chat.id}`).catch(() => null);
    const fresh = page?.messages.find((m) => m.id === Number(message.id));
    if (fresh) { el.replaceWith(workflowElement(fresh)); return; }
  }
  message.run = run;
  el.replaceWith(workflowElement(message));
}

// ---------------------------------------------------------------------------
// The organisation plan card
// ---------------------------------------------------------------------------
function planElement(m) {
  const plan = m.plan;
  const el = h(`<div class="msg plan" data-id="${m.id}" data-plan="${plan?.id || m.data?.plan_id || ''}"><div class="plan-box"></div></div>`);
  const box = $('.plan-box', el);
  if (!plan || plan.status === 'making') {
    box.innerHTML = `<div class="plan-head"><h3>🗂 Organisation plan</h3></div><p>${spinner('Reading the files and preparing a filing plan…')}</p>
      <p class="muted small">Scanned pages are read with OCR, which can take a few seconds a page.</p>`;
    return el;
  }
  if (plan.status === 'applied') {
    const result = plan.data.result || {};
    box.innerHTML = `<div class="plan-head"><h3>🗂 Organisation plan</h3><span class="badge good">Approved & filed</span></div>
      <p>${(result.documents || []).length} document(s) filed${(result.events || []).length ? `, ${(result.events || []).length} date(s) added to the calendar` : ''}.</p>
      <details><summary>Show what was filed</summary><ul class="plain">${(result.documents || []).map((d) => `<li><button class="chip doc verified" data-doc="${d.id}">📄 ${esc(d.title)}</button> <span class="muted small">${esc(d.folder)} / ${esc(d.filename)}</span></li>`).join('')}</ul></details>`;
    return el;
  }
  if (plan.status === 'discarded') {
    box.innerHTML = `<div class="plan-head"><h3>🗂 Organisation plan</h3><span class="badge">Discarded</span></div><p class="muted small">Nothing was moved. The uploaded files are still kept in the case's _originals folder.</p>`;
    return el;
  }
  const edit = planEdits[plan.id] || (planEdits[plan.id] = JSON.parse(JSON.stringify(plan.data)));
  renderPlanEditor(box, plan, edit);
  return el;
}

function folderOptions(current, edit) {
  const all = [...new Set([...FOLDERS, ...edit.documents.map((d) => d.folder)])];
  return all.map((f) => `<option ${f === current ? 'selected' : ''}>${esc(f)}</option>`).join('') + '<option value="__new">+ New folder…</option>';
}

function renderPlanEditor(box, plan, edit) {
  const docs = edit.documents;
  const events = edit.events || [];
  const caseItems = Object.values(edit.case || {});
  box.innerHTML = `
    <div class="plan-head"><h3>🗂 Organisation plan</h3>
      <span class="badge ${plan.made_by === 'ai' ? 'info' : 'warn'}">${plan.made_by === 'ai' ? 'Made by AI' : 'Made with keywords'}</span>
      <span class="muted small">Nothing is moved until you approve.</span></div>
    ${edit.summary ? `<p>${esc(edit.summary)}</p>` : ''}
    ${(edit.notes || []).length ? `<div class="plan-notes">${edit.notes.map((n) => `<p>⚠ ${esc(n)}</p>`).join('')}</div>` : ''}
    <h4>Documents (${docs.length})</h4>
    <div class="plan-docs">${docs.map((d, i) => `
      <div class="plan-doc ${d.include ? '' : 'off'}" data-i="${i}">
        <label class="inc"><input type="checkbox" data-f="include" ${d.include ? 'checked' : ''}> File it</label>
        <div class="pd-main">
          <div class="pd-row"><input class="pd-title" data-f="title" value="${esc(d.title)}" aria-label="Title">
            <input class="pd-type" data-f="doc_type" value="${esc(d.doc_type)}" list="doc-types" aria-label="Type">
            <input type="date" data-f="date" value="${esc(d.date)}" aria-label="Date"></div>
          <div class="pd-row"><span class="pd-label">📁</span><select data-f="folder" aria-label="Folder">${folderOptions(d.folder, edit)}</select>
            <input class="pd-file" data-f="filename" value="${esc(d.filename)}" aria-label="New file name"></div>
          <div class="pd-row muted small">From <b>${esc(d.source_name)}</b>${d.kind === 'pdf' && d.total_pages > 1
            ? ` · pages <input type="number" class="pg" min="1" max="${d.total_pages}" data-f="first_page" value="${d.first_page}"> to <input type="number" class="pg" min="1" max="${d.total_pages}" data-f="last_page" value="${d.last_page}"> of ${d.total_pages}`
            : ` · ${d.total_pages} page${d.total_pages === 1 ? '' : 's'}`}
            ${d.parties?.length ? ` · ${esc(d.parties.slice(0, 4).join(', '))}` : ''}</div>
          ${d.summary ? `<div class="pd-summary small">${esc(d.summary)}</div>` : ''}
        </div></div>`).join('')}</div>
    <datalist id="doc-types">${['FIR', 'Charge-sheet', 'Order', 'Bail order', 'Judgment', 'Petition', 'Plaint', 'Written statement', 'Reply', 'Application', 'Bail application',
      'Affidavit', 'Vakalatnama', 'Legal notice', 'Notice', 'Summons', 'Agreement', 'Medical record', 'Identity document', 'Case status (eCourts)', 'Letter / email', 'Document']
      .map((t) => `<option value="${t}">`).join('')}</datalist>
    <h4>Dates for your calendar (${events.length})</h4>
    ${events.length ? `<div class="plan-events">${events.map((e, i) => `
      <div class="plan-event ${e.include ? '' : 'off'}" data-e="${i}">
        <label class="inc"><input type="checkbox" data-ef="include" ${e.include ? 'checked' : ''}> Add</label>
        <select data-ef="kind">${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}" ${k === e.kind ? 'selected' : ''}>${v}</option>`).join('')}</select>
        <input data-ef="title" value="${esc(e.title)}" aria-label="What">
        <input type="date" data-ef="date" value="${esc(e.date)}"><input type="time" data-ef="time" value="${esc(e.time)}">
        <input data-ef="location" value="${esc(e.location)}" placeholder="Where" aria-label="Where">
        ${e.quote ? `<div class="quote small">“${esc(e.quote)}”${e.exists ? ' <span class="badge">already in calendar</span>' : ''}${e.past ? ' <span class="badge warn">date has passed</span>' : ''}</div>` : ''}
      </div>`).join('')}</div>` : '<p class="muted small">No hearing dates or deadlines were found in these files.</p>'}
    ${caseItems.length ? `<h4>Case details found</h4><div class="plan-case">${caseItems.map((s) => `
      <label class="plan-field"><input type="checkbox" data-cf="${s.field}" ${s.apply ? 'checked' : ''}>
        <span class="pf-label">${esc(FIELD_LABELS[s.field] || s.field)}</span>
        <input data-cv="${s.field}" value="${esc(s.value)}">
        ${s.current ? `<span class="muted small">now: ${esc(s.current)}</span>` : ''}</label>`).join('')}</div>` : ''}
    <div class="plan-actions">
      <button class="btn primary" data-approve>✓ Approve & organise</button>
      <button class="btn" data-discard>Discard</button>
      <span class="spacer"></span>
    </div>
    <form class="revise"><input placeholder="Or ask the AI to change the plan, e.g. “put all orders in one folder called Court Orders”" maxlength="2000">
      <button class="btn">Ask AI</button></form>`;

  // Keep the edit object in step with the inputs
  $$('.plan-doc', box).forEach((row) => {
    const d = docs[Number(row.dataset.i)];
    $$('[data-f]', row).forEach((input) => {
      input.onchange = () => {
        const field = input.dataset.f;
        if (field === 'include') { d.include = input.checked; row.classList.toggle('off', !d.include); return; }
        if (field === 'folder' && input.value === '__new') {
          const name = prompt('Name of the new folder', '10 ');
          if (name && name.trim()) { d.folder = name.trim(); renderPlanEditor(box, plan, edit); } else input.value = d.folder;
          return;
        }
        d[field] = ['first_page', 'last_page'].includes(field) ? Number(input.value) : input.value;
      };
    });
  });
  $$('.plan-event', box).forEach((row) => {
    const e = events[Number(row.dataset.e)];
    $$('[data-ef]', row).forEach((input) => {
      input.onchange = () => {
        if (input.dataset.ef === 'include') { e.include = input.checked; row.classList.toggle('off', !e.include); } else e[input.dataset.ef] = input.value;
      };
    });
  });
  $$('[data-cf]', box).forEach((input) => { input.onchange = () => { edit.case[input.dataset.cf].apply = input.checked; }; });
  $$('[data-cv]', box).forEach((input) => { input.onchange = () => { edit.case[input.dataset.cv].value = input.value; edit.case[input.dataset.cv].apply = true; renderPlanEditor(box, plan, edit); }; });

  $('[data-approve]', box).onclick = async (event) => {
    const button = event.currentTarget;
    button.disabled = true; button.innerHTML = spinner('Filing…');
    try {
      await api(`/api/plans/${plan.id}/apply`, { method: 'POST', body: { documents: edit.documents, events: edit.events, case: edit.case } });
      delete planEdits[plan.id];
      toast('Files organised. Dates added to your calendar.', 'success');
      window.dispatchEvent(new CustomEvent('case-files-changed'));
    } catch (error) { toastError(error); button.disabled = false; button.textContent = '✓ Approve & organise'; }
  };
  $('[data-discard]', box).onclick = async () => {
    if (!(await confirmBox('Discard this plan? Nothing will be moved; the uploads stay in _originals.', 'Discard'))) return;
    await api(`/api/plans/${plan.id}/discard`, { method: 'POST' }).catch(toastError);
  };
  $('.revise', box).onsubmit = async (event) => {
    event.preventDefault();
    const input = $('input', event.target);
    const instruction = input.value.trim();
    if (!instruction) return;
    const button = $('button', event.target);
    button.disabled = true; button.innerHTML = spinner('Revising…');
    try {
      const revised = await api(`/api/plans/${plan.id}/revise`, {
        method: 'POST', body: { instruction, current: { documents: edit.documents, events: edit.events, case: edit.case } },
      });
      planEdits[plan.id] = JSON.parse(JSON.stringify(revised.data));
      renderPlanEditor(box, revised, planEdits[plan.id]);
      toast('Plan revised. Check it, then approve.', 'success');
    } catch (error) { toastError(error); button.disabled = false; button.textContent = 'Ask AI'; }
  };
}

// ---------------------------------------------------------------------------
// Tasks: click -> small form -> Start -> the result (streamed).
// "Show me the steps first" adds a step: the AI adapts the steps, you edit and approve them.
// ---------------------------------------------------------------------------
async function usableTasks() {
  const skills = await api('/api/skills');
  return skills.filter((s) => (view.case ? s.where !== 'general' : s.where !== 'case'));
}

async function fillTaskButtons(container) {
  const holder = $('[data-tasks]', container);
  if (!holder) return;
  try {
    const tasks = await usableTasks();
    holder.innerHTML = tasks.map((s) => `<button class="task-btn" data-task="${esc(s.id)}" title="${esc((s.purpose || '').split('\n')[0])}">
      <span class="task-icon">${esc(s.icon)}</span><span>${esc(s.title)}</span></button>`).join('');
    $$('[data-task]', holder).forEach((b) => { b.onclick = () => workflowForm(tasks.find((s) => s.id === b.dataset.task)); });
  } catch { holder.closest('.task-strip')?.remove(); }
}

export async function workflowPicker(preselect = null, prefill = {}) {
  if (!view) return;
  const usable = await usableTasks();
  if (preselect) { const skill = usable.find((s) => s.id === preselect); if (skill) { workflowForm(skill, prefill); return; } }
  const m = modal('Start a task', `<div class="skill-grid">${usable.map((s) => `
    <button class="skill-card" data-skill="${esc(s.id)}"><span class="skill-icon">${esc(s.icon)}</span>
      <b>${esc(s.title)}</b><span class="muted small">${esc((s.purpose || '').split('\n')[0].slice(0, 140))}</span>
      <span class="badge">${s.output === 'draft' ? 'Draft' : 'Report'}</span></button>`).join('')}</div>`, { wide: true });
  $$('[data-skill]', m.root).forEach((b) => { b.onclick = () => { m.close(); workflowForm(usable.find((s) => s.id === b.dataset.skill), prefill); }; });
}

function workflowForm(skill, prefill = {}) {
  if (!skill || !view) return;
  const documents = view.case ? null : view.documents;
  const fields = skill.inputs.map((i) => `<label>${esc(i.label)}
    ${i.type === 'textarea' ? `<textarea name="${esc(i.key)}" rows="3">${esc(prefill[i.key] || '')}</textarea>`
      : `<input name="${esc(i.key)}" type="${i.type === 'date' ? 'date' : 'text'}" value="${esc(prefill[i.key] || '')}" placeholder="${esc(i.placeholder || '')}">`}</label>`).join('');
  const m = modal(`${skill.icon} ${skill.title}`, `<form class="form" id="wf-form">
      <p class="muted">${esc((skill.purpose || '').trim())}</p>
      ${fields}
      ${skill.documents !== 'none' ? `<fieldset><legend>Documents to use ${skill.documents === 'required' ? '(at least one)' : '(optional)'}</legend>
        <div class="doc-picker">${spinner()}</div></fieldset>` : ''}
      <div class="actions"><label class="check small" title="The AI first adapts the steps to this matter, and may ask you up to 3 questions. You approve the steps before it starts.">
          <input type="checkbox" name="review_steps"> Show me the steps first</label>
        <span class="spacer"></span>
        <button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary" data-go>Start ▶</button></div>
    </form>`, { wide: true });
  $('[data-cancel]', m.root).onclick = m.close;
  const review = $('[name=review_steps]', m.root);
  review.onchange = () => { $('[data-go]', m.root).textContent = review.checked ? 'Show the steps →' : 'Start ▶'; };
  const picker = $('.doc-picker', m.root);
  if (picker) {
    const load = view.case ? api(`/api/cases/${view.case.id}/documents`).then((r) => r.folders.flatMap((f) => f.documents)) : Promise.resolve(documents || []);
    load.then((docs) => {
      picker.innerHTML = docs.length ? docs.map((d) => `<label class="doc-pick"><input type="checkbox" name="doc" value="${d.id}">
        ${esc(d.title)} <span class="muted small">${esc(d.doc_type || '')} ${d.doc_date ? '· ' + esc(niceDate(d.doc_date, false)) : ''}</span></label>`).join('')
        : `<p class="muted small">No documents here yet. ${skill.documents === 'required' ? 'Upload the document into this chat first (📎).' : ''}</p>`;
    });
  }
  $('#wf-form').onsubmit = async (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const reviewSteps = form.has('review_steps');
    const inputs = {};
    for (const [key, value] of form.entries()) if (key !== 'doc' && key !== 'review_steps') inputs[key] = value;
    inputs.document_ids = form.getAll('doc').map(Number);
    if (skill.documents === 'required' && !inputs.document_ids.length) { toast('Choose at least one document (or upload it first).', 'warn'); return; }
    const button = $('[data-go]', m.root);
    button.disabled = true;
    try {
      const result = await api('/api/runs', {
        method: 'POST', body: { chat_id: view.chat.id, skill: skill.id, inputs, review_steps: reviewSteps },
      });
      m.close();
      const message = { ...result.message, run: result.run };
      addOrReplaceMessage(message);
      if (!reviewSteps) {
        // Straight to work: run the task's own steps now
        const el = $(`.msg.workflow[data-id="${message.id}"]`, view.root);
        if (el) executeRun(el, result.run, result.run.plan?.steps || [], {}, message.data || {});
      }
    } catch (error) { toastError(error); button.disabled = false; }
  };
}

export function startHearingBrief(event) {
  const go = () => workflowPicker('hearing-brief', { hearing: `${niceDate(event.date)}${event.time ? ' ' + event.time : ''} - ${event.title}` });
  if (view?.case?.id === event.case_id) { go(); return; }
  window.addEventListener('chat-ready', go, { once: true });
  location.hash = `#/case/${event.case_id}`;
}

const STATUS_LABEL = { planning: 'preparing the steps', waiting: 'waiting for you', running: 'working', done: 'done', failed: 'stopped', cancelled: 'cancelled' };

function workflowElement(m) {
  const run = m.run || {};
  const data = m.data || {};
  const el = h(`<div class="msg workflow" data-id="${m.id}" data-run="${run.id || data.run_id}"><div class="wf-box">
    <div class="wf-head"><h3>${esc(data.icon || '📋')} ${esc(data.title || run.skill || 'Task')}</h3><span class="badge wf-status">${esc(STATUS_LABEL[run.status] || run.status || '')}</span></div>
    <div class="wf-body"></div></div></div>`);
  const body = $('.wf-body', el);
  const status = run.status;
  if (status === 'planning' || !status) {
    body.innerHTML = `<p>${spinner('Planning the steps for this matter…')}</p>`;
  } else if (status === 'waiting') {
    const plan = run.plan || {};
    const steps = (plan.steps || []).map((s) => ({ ...s }));
    const draw = () => {
      body.innerHTML = `${plan.summary ? `<p>${esc(plan.summary)}</p>` : ''}
        ${plan.note ? `<p class="warn small">${esc(plan.note)}</p>` : ''}
        <p class="muted small">Edit, remove or add steps, then approve. Nothing runs before you approve.</p>
        <ol class="wf-steps">${steps.map((s, i) => `<li><input data-step="${i}" value="${esc(s.title)}" aria-label="Step ${i + 1}">
          ${s.detail ? `<div class="muted small">${esc(s.detail)}</div>` : ''}<button class="icon-btn small" data-remove="${i}" title="Remove step">✕</button></li>`).join('')}</ol>
        <button class="link small" data-add-step>+ Add a step</button>
        ${(plan.questions || []).length ? `<h4>Questions before I start</h4>${plan.questions.map((q, i) => `<label class="wf-q">${esc(q)}<input data-q="${i}" placeholder="Your answer (optional)"></label>`).join('')}` : ''}
        <div class="plan-actions"><button class="btn primary" data-run>▶ Approve & run</button><button class="btn" data-cancel>Cancel</button></div>`;
      $$('[data-step]', body).forEach((input) => { input.oninput = () => { steps[Number(input.dataset.step)].title = input.value; }; });
      $$('[data-remove]', body).forEach((b) => { b.onclick = () => { steps.splice(Number(b.dataset.remove), 1); draw(); }; });
      $('[data-add-step]', body).onclick = () => { steps.push({ title: '', detail: '' }); draw(); $$('[data-step]', body).pop()?.focus(); };
      $('[data-cancel]', body).onclick = async () => { await api(`/api/runs/${run.id}/cancel`, { method: 'POST' }).catch(toastError); };
      $('[data-run]', body).onclick = () => {
        const answers = {};
        $$('[data-q]', body).forEach((input) => { answers[plan.questions[Number(input.dataset.q)]] = input.value; });
        executeRun(el, run, steps.filter((s) => s.title.trim()), answers, data);
      };
    };
    draw();
  } else if (status === 'running') {
    body.innerHTML = `<p>${spinner('Running…')}</p>`;
  } else if (status === 'cancelled') {
    body.innerHTML = '<p class="muted">Cancelled.</p>';
  } else {
    body.innerHTML = `<div data-id="${m.id}"><div class="answer"><div class="answer-body"></div><div class="answer-foot"></div></div></div>`;
    const holder = $('[data-id]', body);
    const output = m.content || run.output || '';
    fillAnswer(holder, output, status === 'failed' && !output ? { error: 'The task did not finish. Try again.' } : data);
  }
  return el;
}

async function executeRun(el, run, steps, answers, data) {
  el.classList.add('running');
  $('.wf-status', el).textContent = STATUS_LABEL.running;
  const body = $('.wf-body', el);
  body.innerHTML = `<div class="wf-progress">${steps.map((s, i) => `<div class="wf-p" data-p="${i + 1}"><span class="n">${i + 1}</span> ${esc(s.title)}</div>`).join('')}</div>
    <div data-id="${el.dataset.id}"><div class="answer"><div class="answer-body">${spinner('Starting…')}</div><div class="answer-foot"></div></div></div>`;
  const holder = $('[data-id]', body);
  let text = '';
  let sources = null;
  let last = 0;
  const mark = () => {
    const done = [...text.matchAll(/##\s*Step\s*(\d+)/gi)].map((x) => Number(x[1]));
    const current = done.length ? Math.max(...done) : 0;
    $$('.wf-p', body).forEach((p) => {
      const n = Number(p.dataset.p);
      p.classList.toggle('done', n < current || /##\s*Result/i.test(text));
      p.classList.toggle('now', n === current && !/##\s*Result/i.test(text));
    });
  };
  try {
    await streamPost(`/api/runs/${run.id}/execute`, { steps, answers }, {
      sources: (s) => { sources = s; },
      delta: (d) => {
        text += d.text;
        if (performance.now() - last > 100) { last = performance.now(); fillAnswer(holder, text, { sources }, true); mark(); }
      },
      done: (d) => {
        fillAnswer(holder, text, { ...data, sources, citations: d.citations, model: d.model, cost: d.cost });
        mark(); $('.wf-status', el).textContent = STATUS_LABEL.done;
      },
      error: (d) => { fillAnswer(holder, text, { sources, error: d.message }); $('.wf-status', el).textContent = STATUS_LABEL.failed; },
    });
  } catch (error) {
    fillAnswer(holder, text, { sources, error: error.message });
  } finally {
    el.classList.remove('running');
  }
}
