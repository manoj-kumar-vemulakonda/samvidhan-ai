// pages.js - the home screen, law library, IPC -> BNS converter, workflows (Skills) and settings.

import {
  $, $$, S, api, confirmBox, esc, modal, niceDate, relativeDay, showArticle, showJudgment, showSection, spinner,
  textBlocks, toast, toastError,
} from './core.js';
import { loadSidebar, newCaseDialog } from './side.js';

// ---------------------------------------------------------------------------
// Home
// ---------------------------------------------------------------------------
export async function renderHome(root) {
  const name = S.settings?.display_name;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  const agenda = await api('/api/agenda?days=7').catch(() => ({ upcoming: [], overdue: [] }));
  const today = agenda.upcoming.filter((e) => e.date === agenda.today);
  const recent = S.cases.filter((c) => c.status === 'active').slice(0, 6);
  root.innerHTML = `<div class="home">
    <h1>${greeting}${name ? `, ${esc(name)}` : ''}</h1>
    <p class="muted">${today.length ? `You have ${today.length} item${today.length > 1 ? 's' : ''} today.` : 'Nothing in your calendar today.'}
      ${agenda.overdue.length ? `<span class="warn">${agenda.overdue.length} overdue.</span>` : ''}</p>
    <form class="home-ask"><textarea rows="2" placeholder="Ask anything about Indian law… (starts a general chat)" aria-label="Question"></textarea><button class="btn primary">Ask ➤</button></form>
    <div class="drop-box" tabindex="0" aria-label="Drop your case files here">
      <span class="drop-icon">📂</span>
      <div class="drop-text"><b>Drop your case files here</b> <span class="muted">- from one case or many</span>
        <div class="muted small">I'll sort them into cases, name and file them, and find the hearing dates for your calendar. Nothing changes until you approve.</div></div>
      <label class="btn">Choose files<input type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.jpg,.jpeg,.png,.webp,.tif,.tiff,.bmp"></label>
    </div>
    <div class="intakes"></div>
    <div class="home-actions">
      <button class="tile" data-new-case><span>📁</span><b>New case</b><small>One case = its own chat, files and dates</small></button>
      <button class="tile" data-go="#/convert"><span>🔁</span><b>IPC → BNS</b><small>Convert old sections to the new laws</small></button>
      <button class="tile" data-go="#/library"><span>📚</span><b>Law library</b><small>${S.status?.acts || 0} Acts and court rule books, and the Constitution</small></button>
      <button class="tile" data-go="#/workflows"><span>⚡</span><b>Workflows</b><small>FIR check, bail, notices, RTI…</small></button>
    </div>
    ${recent.length ? `<h3>Your cases</h3><div class="case-cards">${recent.map((c) => `<a class="case-card" href="#/case/${c.id}">
      <b>${esc(c.title)}</b><span class="muted small">${esc([c.court, c.case_number].filter(Boolean).join(' · ') || ' ')}</span>
      ${c.next_event ? `<span class="small">Next: ${esc(relativeDay(c.next_event.date))} · ${esc(c.next_event.title)}</span>` : '<span class="muted small">No upcoming date</span>'}</a>`).join('')}</div>` : ''}
  </div>`;
  $('[data-new-case]', root).onclick = () => newCaseDialog();
  $$('[data-go]', root).forEach((b) => { b.onclick = () => { location.hash = b.dataset.go; }; });
  setupDropBox(root);
  loadIntakes().catch(() => {});
  const form = $('.home-ask', root);
  const box = $('textarea', form);
  box.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const question = box.value.trim();
    if (!question) return;
    const chat = await api('/api/chats', { method: 'POST', body: { title: 'New chat' } });
    await loadSidebar();
    window.addEventListener('chat-ready', () => import('./chat.js').then((m) => m.setComposerText(question, true)), { once: true });
    location.hash = `#/chat/${chat.id}`;
  };
}

// ---------------------------------------------------------------------------
// The drop box: a pile of files from one case or many -> sorted into cases -> "Approve all"
// ---------------------------------------------------------------------------
function setupDropBox(root) {
  const box = $('.drop-box', root);
  const input = $('input[type=file]', box);
  input.onchange = () => { sendIntake([...input.files]); input.value = ''; };
  box.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } };
  let depth = 0;
  const home = $('.home', root);
  home.addEventListener('dragenter', (e) => { if (e.dataTransfer.types.includes('Files')) { depth += 1; box.classList.add('over'); } });
  home.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) box.classList.remove('over'); });
  home.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
  home.addEventListener('drop', (e) => {
    if (!e.dataTransfer.files.length) return;
    e.preventDefault(); depth = 0; box.classList.remove('over');
    sendIntake([...e.dataTransfer.files]);
  });
}

async function sendIntake(fileList) {
  if (!fileList.length) return;
  const max = (S.status?.max_upload_mb || 60) * 1024 * 1024;
  const big = fileList.filter((f) => f.size > max);
  if (big.length) toast(`Too large (over ${S.status?.max_upload_mb || 60} MB): ${big.map((f) => f.name).join(', ')}`, 'warn');
  const form = new FormData();
  fileList.filter((f) => f.size <= max).forEach((f) => form.append('uploads', f, f.name));
  if (!form.has('uploads')) return;
  try {
    const response = await fetch('/api/intake', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'The upload failed.');
    (data.data.skipped || []).forEach((s) => toast(`${s.name}: ${s.reason}`, 'warn', 8000));
    showIntake(data);
  } catch (error) { toastError(error); }
}

async function loadIntakes() {
  const list = $('.home .intakes');
  if (!list) return;
  const intakes = await api('/api/intake');
  list.innerHTML = '';
  intakes.forEach(showIntake);
}

function showIntake(intake) {
  const list = $('.home .intakes');
  if (!list) return;
  const card = intakeCard(intake);
  const old = $(`[data-intake="${intake.id}"]`, list);
  if (['discarded', 'closed'].includes(intake.status)) { old?.remove(); return; }
  if (old) old.replaceWith(card); else list.prepend(card);
}

export async function onIntake(event) {
  if (!$('.home .intakes')) return;
  const card = $(`[data-intake="${event.intake_id}"]`);
  // While you are checking a sorted pile, don't redraw it under your hands
  if (card && card.dataset.status === 'ready' && event.status === 'ready') return;
  try { showIntake(await api(`/api/intake/${event.intake_id}`)); } catch (_) { /* gone */ }
}

const INTAKE_BADGE = { reading: 'Reading the files', sorting: 'Sorting into cases', applying: 'Filing', failed: 'Problem' };

function intakeCard(intake) {
  const data = intake.data || {};
  const fileCount = (data.files || []).length;
  const card = document.createElement('div');
  card.className = 'intake-card';
  card.dataset.intake = intake.id;
  card.dataset.status = intake.status;
  if (['reading', 'sorting', 'applying'].includes(intake.status)) {
    card.innerHTML = `<div class="plan-head"><h3>📂 ${fileCount} file${fileCount === 1 ? '' : 's'}</h3>
      <span class="badge info">${esc(INTAKE_BADGE[intake.status])}</span></div>
      <p>${spinner(intake.progress)}</p>
      <p class="muted small">You can keep working - this card updates by itself.</p>`;
    return card;
  }
  if (intake.status === 'failed') {
    card.innerHTML = `<div class="plan-head"><h3>📂 ${fileCount} file${fileCount === 1 ? '' : 's'}</h3><span class="badge bad">Problem</span></div>
      <p>${esc(intake.progress)}</p><div class="plan-actions"><button class="btn" data-discard>Remove</button></div>`;
    $('[data-discard]', card).onclick = () => discardIntake(intake.id);
    return card;
  }
  if (intake.status === 'applied') {
    const rows = (data.result || []).map((r) => r.error
      ? `<li class="warn">${r.case_id ? `<a href="#/case/${r.case_id}">${esc(r.title)}</a>` : esc(r.title)}: ${esc(r.error)}</li>`
      : `<li><a href="#/case/${r.case_id}">${esc(r.title)}</a> <span class="badge ${r.new ? 'info' : ''}">${r.new ? 'new case' : 'existing case'}</span>
         <span class="muted small">· ${r.documents} document${r.documents === 1 ? '' : 's'} filed${r.events ? ` · ${r.events} date${r.events === 1 ? '' : 's'} added to your calendar` : ''}</span></li>`).join('');
    card.innerHTML = `<div class="plan-head"><h3>✓ Filed</h3><span class="badge good">Done</span></div>
      <ul class="intake-result">${rows}</ul>
      <div class="plan-actions"><button class="btn small" data-close>OK</button></div>`;
    $('[data-close]', card).onclick = async () => { await api(`/api/intake/${intake.id}/close`, { method: 'POST' }).catch(() => {}); card.remove(); };
    return card;
  }
  // ready: the sorted pile, waiting for "Approve all"
  const groups = data.groups || [];
  const names = Object.fromEntries((data.files || []).map((f) => [f.id, f.name]));
  const caseOptions = (selected) => `<option value="0" ${selected ? '' : 'selected'}>➕ New case</option>`
    + (S.cases || []).map((c) => `<option value="${c.id}" ${c.id === selected ? 'selected' : ''}>📁 ${esc(c.title)}</option>`).join('');
  const moveOptions = (group) => `<option value="">Move to…</option>`
    + groups.filter((g) => g.key !== group.key).map((g) => `<option value="${esc(g.key)}">${esc(g.title)}</option>`).join('')
    + '<option value="new">A new case</option>';
  const groupHtml = (group) => {
    const plan = group.plan || {};
    const files = group.upload_ids.map((uid) => {
      const docs = (plan.documents || []).filter((d) => d.upload_id === uid);
      const lines = docs.map((d) => `<div class="small ${d.include === false ? 'muted' : ''}">${esc(d.title)}
        <span class="muted">→ ${esc(d.folder)}${d.date ? ` · ${esc(niceDate(d.date, false))}` : ''}${d.include === false ? ' · left out' : ''}</span></div>`).join('');
      return `<div class="ig-file"><div class="ig-file-main">📄 <b>${esc(names[uid] || 'file')}</b>${lines}</div>
        <select data-move="${uid}" aria-label="Move this file">${moveOptions(group)}</select></div>`;
    }).join('');
    const events = (plan.events || []).map((e) => `<label class="plan-event ${e.include ? '' : 'off'}">
        <input type="checkbox" data-event="${esc(e.key)}" ${e.include ? 'checked' : ''}>
        <b>${esc(niceDate(e.date))}${e.time ? ` · ${esc(e.time)}` : ''}</b> ${esc(e.title)}
        <span class="badge">${esc(e.kind)}</span>${e.exists ? ' <span class="badge">already in calendar</span>' : ''}
        ${e.quote ? `<div class="quote small">“${esc(e.quote)}”</div>` : ''}</label>`).join('');
    const details = ['court', 'case_number', 'cnr'].map((f) => plan.case?.[f]?.value).filter(Boolean);
    const notes = (plan.notes || []).map((n) => `<p>⚠ ${esc(n)}</p>`).join('');
    return `<div class="intake-group" data-key="${esc(group.key)}">
      <div class="ig-head">
        <input type="checkbox" data-include checked title="File this case" aria-label="File this case">
        <input class="ig-title" value="${esc(group.title)}" aria-label="Case name" ${group.case_id ? 'disabled' : ''}>
        <select data-case aria-label="New or existing case">${caseOptions(group.case_id)}</select>
      </div>
      ${group.reason ? `<p class="muted small">${esc(group.reason)}</p>` : ''}
      ${details.length && !group.case_id ? `<p class="small">${details.map(esc).join(' · ')}</p>` : ''}
      <div class="ig-files">${files}</div>
      ${events ? `<div class="ig-dates"><div class="small"><b>Dates for your calendar</b> (untick any that are wrong)</div>${events}</div>`
        : '<p class="muted small">No hearing date or deadline found in these files.</p>'}
      ${notes ? `<div class="plan-notes">${notes}</div>` : ''}
    </div>`;
  };
  const topNotes = (data.notes || []).map((n) => `<p>⚠ ${esc(n)}</p>`).join('');
  card.innerHTML = `<div class="plan-head"><h3>📂 ${fileCount} file${fileCount === 1 ? '' : 's'} → ${groups.length} case${groups.length === 1 ? '' : 's'}</h3>
      <span class="badge">${data.made_by === 'ai' ? 'Sorted by AI' : 'Sorted by case numbers'}</span>
      <span class="muted small">Nothing is created or moved until you approve.</span></div>
    ${topNotes ? `<div class="plan-notes">${topNotes}</div>` : ''}
    ${groups.map(groupHtml).join('')}
    <div class="plan-actions"><button class="btn primary" data-approve>✓ Approve all</button><button class="btn" data-discard>Discard</button>
      <span class="muted small">Creates the new cases, files every document and adds the ticked dates to your calendar.</span></div>`;

  $$('[data-case]', card).forEach((select) => {
    select.onchange = () => {
      const group = select.closest('.intake-group');
      const title = $('.ig-title', group);
      const chosen = (S.cases || []).find((c) => c.id === Number(select.value));
      title.disabled = Boolean(chosen);
      if (chosen) title.value = chosen.title;
    };
  });
  $$('[data-include]', card).forEach((box) => { box.onchange = () => box.closest('.intake-group').classList.toggle('off', !box.checked); });
  $$('[data-event]', card).forEach((box) => { box.onchange = () => box.closest('.plan-event').classList.toggle('off', !box.checked); });
  $$('[data-move]', card).forEach((select) => {
    select.onchange = async () => {
      if (!select.value) return;
      select.disabled = true;
      try {
        showIntake(await api(`/api/intake/${intake.id}/move`, { method: 'POST', body: { upload_id: Number(select.dataset.move), to: select.value } }));
      } catch (error) { toastError(error); select.disabled = false; select.value = ''; }
    };
  });
  $('[data-discard]', card).onclick = () => discardIntake(intake.id);
  $('[data-approve]', card).onclick = async (event) => {
    const button = event.currentTarget;
    const edits = $$('.intake-group', card).map((g) => ({
      key: g.dataset.key,
      title: $('.ig-title', g).value.trim(),
      include: $('[data-include]', g).checked,
      case_id: Number($('[data-case]', g).value) || 0,
      events: Object.fromEntries($$('[data-event]', g).map((b) => [b.dataset.event, b.checked])),
    }));
    if (!edits.some((e) => e.include)) { toast('Tick at least one case, or press Discard.', 'warn'); return; }
    button.disabled = true;
    button.innerHTML = spinner('Filing…');
    try {
      const done = await api(`/api/intake/${intake.id}/apply`, { method: 'POST', body: { groups: edits } });
      showIntake(done);
      toast('Files sorted and filed. Dates added to your calendar.', 'success');
      loadSidebar().catch(() => {});
      window.dispatchEvent(new Event('case-files-changed'));
    } catch (error) { toastError(error); button.disabled = false; button.textContent = '✓ Approve all'; }
  };
  return card;
}

async function discardIntake(intakeId) {
  if (!(await confirmBox('Discard these files? Nothing will be filed; they are moved to the workspace\'s _deleted folder.', 'Discard'))) return;
  try {
    await api(`/api/intake/${intakeId}/discard`, { method: 'POST' });
    $(`[data-intake="${intakeId}"]`)?.remove();
  } catch (error) { toastError(error); }
}

export function onboarding() {
  const s = S.settings || {};
  const m = modal('Welcome to Samvidhan AI', `<form class="form" id="welcome">
    <p>Your AI workspace for Indian law practice. A few details make the reminders and drafts personal. Everything stays on this PC.</p>
    <div class="grid2"><label>Title <select name="title"><option ${s.title === 'Adv.' ? 'selected' : ''}>Adv.</option><option>Sr. Adv.</option><option>Mr.</option><option>Ms.</option><option>Dr.</option><option value="">(none)</option></select></label>
    <label>Your name <input name="name" value="${esc(s.name || '')}" placeholder="e.g. Priya Sharma" required></label></div>
    <label>Courts you usually appear in <input name="courts" value="${esc(s.courts || '')}" placeholder="e.g. Saket District Court, Delhi High Court"></label>
    <label>Practice areas <input name="practice_areas" value="${esc(s.practice_areas || '')}" placeholder="e.g. criminal, family, cheque bounce"></label>
    <div class="grid2"><label>Morning agenda at <input type="time" name="morning_time" value="${esc(s.morning_time || '08:00')}"></label>
    <label>Evening-before reminder at <input type="time" name="evening_time" value="${esc(s.evening_time || '19:00')}"></label></div>
    <div class="actions"><button type="button" class="btn" data-skip>Later</button><button class="btn primary">Start</button></div></form>`);
  const save = async (data) => {
    S.settings = await api('/api/settings', { method: 'PUT', body: { ...data, onboarded: true } });
  };
  $('[data-skip]', m.root).onclick = async () => { await save({}); m.close(); };
  $('#welcome').onsubmit = async (e) => {
    e.preventDefault();
    await save(Object.fromEntries(new FormData(e.target).entries())).catch(toastError);
    m.close();
    toast(`Welcome, ${S.settings.display_name || 'counsel'}! Create your first case or ask a question.`, 'success');
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  };
}

// ---------------------------------------------------------------------------
// Law library
// ---------------------------------------------------------------------------
let lawToc = null;
const KIND_ICON = { act: '', rules: '⚖ ', handbook: '📘 ' };
export async function renderLibrary(root, actCode = null, sectionId = null) {
  if (!lawToc) lawToc = await api('/api/laws');
  const books = lawToc.categories.reduce((n, g) => n + g.acts.filter((a) => a.kind !== 'act').length, 0);
  const openGroup = (g) => g.acts.some((a) => a.code === actCode) || g.category === 'Criminal law' || (g.category === 'My rule books');
  root.innerHTML = `<div class="library">
    <aside class="lib-nav">
      <form class="lib-search"><input type="search" placeholder="Search all laws and rules…" aria-label="Search all laws"><button class="btn small">Search</button></form>
      <input type="search" class="act-filter" placeholder="Filter Acts and rule books…" aria-label="Filter Acts">
      <div class="lib-groups">
        <details open><summary>Constitution, courts & judgments</summary>
          <a href="#/library/constitution" class="${actCode === 'constitution' ? 'active' : ''}">🇮🇳 Constitution of India</a>
          <a href="#/library/courts" class="${actCode === 'courts' ? 'active' : ''}">🏛 Courts and their rules</a>
          <a href="#/library/judgments" class="${actCode === 'judgments' ? 'active' : ''}">⚖ Landmark judgments (notes)</a></details>
        ${lawToc.categories.map((g) => `<details ${openGroup(g) ? 'open' : ''}><summary>${esc(g.category)} (${g.acts.length})</summary>
          ${g.acts.map((a) => `<a href="#/library/act/${a.code}" data-name="${esc((a.name + ' ' + a.cite + ' ' + (a.court || '')).toLowerCase())}" class="${a.code === actCode ? 'active' : ''} ${a.status === 'repealed' ? 'repealed' : ''}">${KIND_ICON[a.kind] || ''}${esc(a.name)}</a>`).join('')}</details>`).join('')}
      </div>
      <button class="btn small add-book">＋ Add a rule book (PDF)</button>
      <p class="muted small">${lawToc.acts} Acts and rule books (${books} of court rules) · ${lawToc.sections.toLocaleString('en-IN')} sections and rules. Meaning search: ${esc(lawToc.index)}.</p>
    </aside>
    <section class="lib-main">${spinner()}</section></div>`;
  const main = $('.lib-main', root);
  $('.act-filter', root).oninput = (e) => {
    const q = e.target.value.toLowerCase();
    $$('.lib-groups a[data-name]', root).forEach((a) => { a.hidden = q && !a.dataset.name.includes(q); });
    if (q) $$('.lib-groups details', root).forEach((d) => { d.open = true; });
  };
  $('.add-book', root).onclick = () => addRuleBookDialog();
  $('.lib-search', root).onsubmit = (e) => { e.preventDefault(); const q = $('input', e.target).value.trim(); if (q) lawSearch(main, q); };
  if (actCode === 'constitution') return renderConstitution(main);
  if (actCode === 'judgments') return renderJudgments(main);
  if (actCode === 'courts') return renderCourts(main);
  if (actCode) return renderAct(main, actCode, sectionId);
  main.innerHTML = `<div class="lib-intro"><h2>Law library</h2>
    <p>Every Act and rule book here is searched by the chat, so the AI can cite the exact provision: <b>[BNS s. 103]</b>, <b>[CPC O. VII r. 11]</b>, <b>[Limitation Act Art. 54]</b>, <b>[SC Rules O. XXI r. 3]</b>. Click any provision to read it, or open the official PDF at that page.</p>
    <ul class="small">
      <li><b>Central Acts</b> - criminal, civil, family, commercial, tax, labour and more, with the CPC's Orders and Rules, the Limitation Act's periods and the BNSS table of bailable and cognizable offences.</li>
      <li><b>Court rules</b> - the Supreme Court Rules and Handbook, High Court rules, district-court rules and the Gram Nyayalaya (mobile court) rules. See <a href="#/library/courts">Courts and their rules</a> for what is in for each court.</li>
      <li><b>Your rule books</b> - if your court's rules are missing, add the PDF with <b>＋ Add a rule book</b>; the chat will cite them too.</li></ul>
    <p class="muted small">Sources: India Code (indiacode.gov.in) and the courts' own websites, plus ${esc(lawToc.source)}. The Constitution comes from the official PDF.</p></div>`;
}

export function onLibrary(job) {
  if (job.status === 'done') { toast(`📚 ${job.cite || job.name}: ${job.message}`, 'success', 8000); }
  else if (job.status === 'failed') { toast(`${job.cite || job.name}: ${job.message}`, 'error', 10000); }
  else if (job.status === 'removed') { toast('Rule book removed from the library.'); }
  if (['done', 'removed'].includes(job.status)) {
    lawToc = null;
    if (location.hash.startsWith('#/library')) window.dispatchEvent(new HashChangeEvent('hashchange'));
  }
  const line = document.querySelector(`[data-job="${job.code}"]`);
  if (line) line.textContent = job.message || '';
}

async function renderCourts(main) {
  const data = await api('/api/laws/courts');
  const book = (b) => `<a href="#/library/act/${esc(b.code)}">${esc(b.name)}</a> <span class="muted small">(${b.count})</span>${b.user ? ' <span class="badge">yours</span>' : ''}`;
  main.innerHTML = `<h2>Courts and their rules</h2>
    <p class="muted">What the library holds for every court. Rules marked <span class="badge warn">not yet in</span> could not be downloaded automatically - download the PDF from the court's site (links below) and add it with <b>Add rule book</b>; the chat will use it like any other law.</p>
    ${data.inbox ? `<p class="job-note">📥 <b>Inbox folder:</b> save rule-book PDFs into <code>${esc(data.inbox)}</code> and they are added automatically within a minute. Name the file after the book, e.g. <i>Bombay High Court Appellate Side Rules 1960.pdf</i> - the court is recognised from the name.</p>` : ''}
    ${(data.jobs || []).filter((j) => j.status === 'reading').map((j) => `<p class="job-note">⏳ ${esc(j.cite)}: <span data-job="${esc(j.code)}">${esc(j.message)}</span></p>`).join('')}
    <div class="courts">${data.courts.map((c) => `<div class="court-card">
      <h3>${esc(c.name)}</h3><p class="muted small">${esc(c.states || '')} · <a href="${esc(c.website)}" target="_blank" rel="noopener">official website ↗</a></p>
      ${c.books.length ? `<ul>${c.books.map((b) => `<li>${book(b)}</li>`).join('')}</ul>` : '<p class="muted small">No rule book in the library yet.</p>'}
      ${(c.missing || []).length ? `<p class="small"><span class="badge warn">not yet in</span> ${c.missing.map(esc).join(' · ')}</p>` : ''}
      ${c.note ? `<p class="muted small">${esc(c.note)}</p>` : ''}
      ${(c.links || []).length ? `<p class="small">Download: ${c.links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join(' · ')}</p>` : ''}
      <button class="btn small" data-add-for="${esc(c.name)}">＋ Add rule book</button></div>`).join('')}</div>
    ${data.my_books.length ? `<h3>Rule books you added</h3><ul>${data.my_books.map((b) => `<li>${book({ ...b, count: b.sections_count, user: true })} <span class="muted small">${esc(b.court || '')}</span></li>`).join('')}</ul>` : ''}
    <p class="muted small">Updated ${esc(data.updated || '')}.</p>`;
  $$('[data-add-for]', main).forEach((b) => { b.onclick = () => addRuleBookDialog(b.dataset.addFor); });
}

export function addRuleBookDialog(court = '') {
  const m = modal('Add a rule book', `<form class="form add-book-form">
      <p class="muted small">Any court's rules, a Civil / Criminal Rules of Practice, practice directions or a handbook - as a PDF (scanned PDFs are read with OCR, which takes a few minutes for a long book). Download it from the court's official website.</p>
      <label>PDF file <input type="file" name="book" accept="application/pdf,.pdf"></label>
      <details class="paste-text"><summary>No PDF? Paste the text of the rules instead</summary>
        <textarea name="text" rows="7" placeholder="Paste the full text (from a bare-act copy, your bar association, a library…). Rule numbers like “1.”, “2.” are found automatically."></textarea></details>
      <label>Full name <input name="name" placeholder="Rules of the High Court of Kerala, 1971" required></label>
      <label>Short name used in citations <input name="cite" placeholder="Kerala HC Rules" required></label>
      <label>Court <input name="court" list="court-names" value="${esc(court)}" placeholder="Kerala High Court"></label>
      <label>Applies in (State) <input name="jurisdiction" placeholder="Kerala"></label>
      <label>How the rules are numbered <select name="numbering">
        <option value="auto">Rule 1, 2, 3… (or work it out)</option><option value="chapters">Chapter + rule (Ch. IV r. 3)</option>
        <option value="orders">Order + rule (O. XXI r. 3)</option><option value="sections">Sections (s. 12)</option>
        <option value="pages">No numbering - one entry per page</option></select></label>
      <datalist id="court-names"></datalist>
      <div class="actions"><button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary">Add to library</button></div></form>`);
  api('/api/laws/courts').then((d) => { $('#court-names', m.root).innerHTML = d.courts.map((c) => `<option value="${esc(c.name)}">`).join(''); }).catch(() => {});
  $('[data-cancel]', m.root).onclick = () => m.close();
  $('form', m.root).onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target;
    if (!form.book.files.length && !form.text.value.trim()) { toast('Choose a PDF, or paste the text of the rules.', 'warn'); return; }
    const button = $('button.primary', m.root);
    button.disabled = true;
    button.innerHTML = spinner('Uploading…');
    try {
      const response = await fetch('/api/laws/books', { method: 'POST', body: new FormData(e.target) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || response.statusText);
      m.close();
      toast('Reading the rule book… you can keep working; it will appear in the library when ready.', 'info', 8000);
      location.hash = '#/library/courts';
    } catch (error) { toastError(error); button.disabled = false; button.textContent = 'Add to library'; }
  };
}

async function lawSearch(main, q) {
  main.innerHTML = spinner('Searching…');
  const result = await api(`/api/laws/search?q=${encodeURIComponent(q)}`).catch((e) => { toastError(e); return null; });
  if (!result) return;
  main.innerHTML = `<h2>Results for “${esc(q)}”</h2>
    ${result.acts.map((hit) => `<div class="hit"><button class="chip verified section" data-cite-id="${esc(hit.id)}" data-cite-kind="section">${esc(hit.label)}</button>
      <b>${esc(hit.title)}</b> <span class="muted small">${esc(hit.act_name)}${hit.act_status === 'repealed' ? ' (repealed)' : ''}</span>
      <p class="small">${esc(hit.snippet)}</p></div>`).join('')}
    ${result.constitution.length ? `<h3>Constitution</h3>${result.constitution.map((hit) => `<div class="hit"><button class="chip verified constitution" data-cite-id="${esc(hit.id)}" data-cite-kind="constitution">${esc(hit.label)}</button> <b>${esc(hit.title)}</b><p class="small">${esc(hit.snippet)}</p></div>`).join('')}` : ''}`;
}

async function renderAct(main, code, sectionId) {
  const act = await api(`/api/laws/acts/${encodeURIComponent(code)}`);
  const chapters = [];
  for (const s of act.sections) {
    const key = s.chapter || '';
    if (!chapters.length || chapters[chapters.length - 1].key !== key) chapters.push({ key, title: s.chapter_title, sections: [] });
    chapters[chapters.length - 1].sections.push(s);
  }
  const example = act.sections.find((s) => s.kind !== 'section') || act.sections[0];
  const isBook = act.kind !== 'act';
  const unit = isBook ? 'rules' : 'sections';
  main.innerHTML = `<div class="act-head"><h2>${KIND_ICON[act.kind] || ''}${esc(act.name)}</h2>
    <p class="muted">${act.sections_count} ${unit} · cited as “${esc(example ? example.label : act.cite)}” · ${esc(act.category)}</p>
    ${act.court ? `<p class="court-of"><span class="badge">${isBook ? 'Court rules' : 'Court'}</span> ${esc(act.court)}${act.jurisdiction && act.jurisdiction !== 'Central' ? ` · applies in ${esc(act.jurisdiction)}` : ''}</p>` : ''}
    ${act.note ? `<p class="${act.status === 'repealed' ? 'warn' : 'muted'}">${esc(act.note)}</p>` : ''}
    <p class="muted small">Source: ${esc(act.source)}${act.as_on ? ` · as on ${esc(act.as_on)}` : ''}</p>
    ${act.pdf_url || (act.official_parts || []).length || act.url || act.user ? `<div class="actions left">
      ${act.pdf_url ? `<a class="btn small" href="${esc(act.pdf_url)}" target="_blank" rel="noopener">📄 Official PDF</a>` : ''}
      ${(act.official_parts || []).map((p) => `<a class="btn small" href="${esc(p.pdf_url)}" target="_blank" rel="noopener">📄 ${esc(p.label)} (official PDF)</a>`).join('')}
      ${act.url ? `<a class="btn small" href="${esc(act.url)}" target="_blank" rel="noopener">Where it was published ↗</a>` : ''}
      ${act.user ? '<button class="btn small danger" data-remove-book>Remove this rule book</button>' : ''}</div>` : ''}
    <input type="search" class="section-filter" placeholder="Filter ${unit} (number or words)…" aria-label="Filter sections"></div>
    <div class="act-sections">${chapters.map((ch) => `<details ${chapters.length < 4 ? 'open' : ''}><summary>${ch.key ? (/^\d/.test(ch.key) ? `Chapter ${esc(ch.key)}` : esc(ch.key)) : (isBook ? 'Rules' : 'Sections')}${ch.title ? ` - ${esc(ch.title)}` : ''} <span class="muted small">(${ch.sections.length})</span></summary>
      <ul>${ch.sections.map((s) => `<li data-text="${esc((s.ref + ' ' + s.number + ' ' + s.title).toLowerCase())}"><button class="link" data-section="${esc(s.id)}"><b>${esc(s.ref)}</b> ${esc(s.title)}</button>${s.status === 'omitted' ? ' <span class="badge">omitted</span>' : ''}</li>`).join('')}</ul></details>`).join('')}</div>`;
  $$('[data-section]', main).forEach((b) => { b.onclick = () => showSection(b.dataset.section); });
  const remove = $('[data-remove-book]', main);
  if (remove) remove.onclick = async () => {
    if (!(await confirmBox(`Remove “${act.name}” from the library? (The PDF is kept in workspace/library/_removed.)`, 'Remove'))) return;
    await api(`/api/laws/books/${encodeURIComponent(act.code)}`, { method: 'DELETE' }).then(() => { location.hash = '#/library/courts'; }).catch(toastError);
  };
  $('.section-filter', main).oninput = (e) => {
    const q = e.target.value.toLowerCase().trim();
    $$('.act-sections li', main).forEach((li) => { li.hidden = q && !li.dataset.text.includes(q); });
    if (q) $$('.act-sections details', main).forEach((d) => { d.open = true; });
  };
  if (sectionId) showSection(sectionId);
}

async function renderConstitution(main) {
  const parts = await api('/api/parts');
  main.innerHTML = `<h2>The Constitution of India</h2><p class="muted small">${esc(S.status?.source || '')}</p>
    <input type="search" class="section-filter" placeholder="Filter (e.g. 21, arrest, Seventh Schedule)…">
    <div class="act-sections">${parts.map((p) => `<details><summary>${p.kind === 'schedule' ? '' : `Part ${esc(p.id)} - `}${esc(p.title)}</summary><ul>${(p.articles || []).map((a) => `<li data-text="${esc(((a.number || a.id) + ' ' + a.title).toLowerCase())}">
      <button class="link" data-article="${esc(a.id)}"><b>${esc(a.label || 'Art. ' + a.id)}</b> ${esc(a.title)}</button>${a.status === 'omitted' ? ' <span class="badge">omitted</span>' : ''}</li>`).join('')}</ul></details>`).join('')}</div>`;
  $$('[data-article]', main).forEach((b) => { b.onclick = () => showArticle(b.dataset.article); });
  $('.section-filter', main).oninput = (e) => {
    const q = e.target.value.toLowerCase().trim();
    $$('.act-sections li', main).forEach((li) => { li.hidden = q && !li.dataset.text.includes(q); });
    $$('.act-sections details', main).forEach((d) => { d.open = Boolean(q); });
  };
}

async function renderJudgments(main) {
  const data = await api('/api/judgments');
  main.innerHTML = `<h2>Landmark judgments</h2><p class="muted small">${esc(data.about || '')}</p>
    <ul class="judgments">${data.cases.map((c) => `<li><button class="link" data-case="${esc(c.id)}"><b>${esc(c.name)}</b> (${esc(c.year)})</button>
      <div class="small muted">${esc(c.held.slice(0, 180))}…</div></li>`).join('')}</ul>`;
  $$('[data-case]', main).forEach((b) => { b.onclick = () => showJudgment(b.dataset.case); });
}

// ---------------------------------------------------------------------------
// IPC -> BNS converter
// ---------------------------------------------------------------------------
export function renderConvert(root, initial = '') {
  root.innerHTML = `<div class="convert">
    <h2>Old law → new law</h2>
    <p class="muted">IPC → BNS, CrPC → BNSS, Evidence Act → BSA, and back. The new laws apply to offences from 1 July 2024. Older cases stay under the old law.</p>
    <form class="convert-form"><input placeholder="e.g. IPC 420, 498A, CrPC 438, 65B Evidence Act, BNS 103" value="${esc(initial)}" aria-label="Section to convert"><button class="btn primary">Convert</button></form>
    <div class="examples">${['IPC 302', 'IPC 420', '498A', 'IPC 124A', 'CrPC 438', 'CrPC 125', 'CrPC 482', 'IEA 65B', 'BNS 103', 'BNSS 482']
      .map((x) => `<button data-ex="${x}">${x}</button>`).join('')}</div>
    <div class="convert-result"></div></div>`;
  const form = $('.convert-form', root);
  const run = async (q) => {
    const out = $('.convert-result', root);
    out.innerHTML = spinner();
    try {
      const r = await api(`/api/convert?q=${encodeURIComponent(q)}`);
      const from = r.from;
      out.innerHTML = `<div class="convert-grid">
        <div class="conv-col"><h3>${esc(from.label || from.id)}</h3><p class="muted">${esc(from.title || '')} · ${esc(from.act_name || '')}</p>
          <div class="law-text small">${textBlocks(r.from_text)}</div></div>
        <div class="conv-arrow">→</div>
        <div class="conv-col">${r.mappings.length ? r.mappings.map((mp) => `
          <h3>${mp.label ? esc(mp.label) : '<span class="warn">No equivalent</span>'}</h3>
          <p><span class="badge ${mp.status === 'Retained' ? 'good' : mp.status === 'Deleted' ? 'bad' : 'warn'}">${esc(mp.status)}</span> ${esc(mp.title || '')}</p>
          ${mp.note ? `<p class="warn small">${esc(mp.note)}</p>` : ''}
          <p class="muted small">${esc(mp.statement)}</p>
          ${mp.text ? `<div class="law-text small">${textBlocks(mp.text)}</div>` : ''}`).join('<hr>')
          : '<p class="muted">No mapping found for this section.</p>'}</div></div>
        <p class="muted small">“Retained” = carried over in substance; “Change” = carried over with changes. Compare the texts before relying on them.</p>`;
    } catch (error) { out.innerHTML = `<p class="warn">${esc(error.message)}</p>`; }
  };
  form.onsubmit = (e) => { e.preventDefault(); const q = $('input', form).value.trim(); if (q) { history.replaceState(null, '', `#/convert?q=${encodeURIComponent(q)}`); run(q); } };
  $$('[data-ex]', root).forEach((b) => { b.onclick = () => { $('input', form).value = b.dataset.ex; form.requestSubmit(); }; });
  if (initial) run(initial);
}

// ---------------------------------------------------------------------------
// Workflows (Skills): browse, edit, add
// ---------------------------------------------------------------------------
const NEW_SKILL = `---
id: my-workflow
title: My workflow
icon: 📋
where: both
documents: optional
output: report
inputs:
- topic | What is it about? | text
---
## Purpose
One or two sentences: what this workflow produces.

## Steps
1. First step.
2. Second step.
3. Third step.

## Instructions
Rules the AI must follow (format, what to check, what to cite).
`;

export async function renderWorkflows(root) {
  const skills = await api('/api/skills');
  root.innerHTML = `<div class="workflows">
    <div class="toolbar"><h2>⚡ Workflows</h2><span class="spacer"></span><button class="btn" data-new>+ New workflow</button></div>
    <p class="muted">A workflow is a recipe for a common task. You pick one in a chat (⚡ button). The AI writes a plan for your matter, you approve it, and it runs step by step. Each workflow is a plain-English file in <code>skills\\</code>, so you can edit it here or in Notepad.</p>
    <div class="skill-grid">${skills.map((s) => `<div class="skill-card static"><span class="skill-icon">${esc(s.icon)}</span>
      <b>${esc(s.title)}</b><span class="muted small">${esc((s.purpose || '').split('\n')[0])}</span>
      <span class="small">${s.where === 'case' ? 'In a case' : s.where === 'general' ? 'General chats' : 'Cases and general chats'} · ${s.output === 'draft' ? 'Draft (Word)' : 'Report'}</span>
      <div class="actions left"><button class="btn small" data-edit="${esc(s.id)}">Edit</button></div></div>`).join('')}</div></div>`;
  $$('[data-edit]', root).forEach((b) => { b.onclick = () => editSkill(b.dataset.edit, root); });
  $('[data-new]', root).onclick = () => editSkill(null, root);
}

async function editSkill(id, root) {
  const skill = id ? await api(`/api/skills/${encodeURIComponent(id)}`) : { source: NEW_SKILL };
  const m = modal(id ? `Edit: ${skill.title}` : 'New workflow', `<form class="form" id="skill-form">
    <textarea name="source" rows="22" class="mono" spellcheck="false">${esc(skill.source)}</textarea>
    <p class="muted small">The header between the --- lines sets the name and the inputs (one per line: <code>- key | Label | text/date/textarea</code>). The <b>## Steps</b> are the default plan; <b>## Instructions</b> are rules for the AI.</p>
    <div class="actions">${id ? '<button type="button" class="btn danger" data-del>Remove</button>' : ''}<span class="spacer"></span>
      <button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary">Save</button></div></form>`, { wide: true });
  $('[data-cancel]', m.root).onclick = m.close;
  const del = $('[data-del]', m.root);
  if (del) del.onclick = async () => {
    if (!(await confirmBox('Remove this workflow? (It is moved to skills\\_removed.)', 'Remove'))) return;
    await api(`/api/skills/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(toastError);
    m.close(); renderWorkflows(root);
  };
  $('#skill-form').onsubmit = async (e) => {
    e.preventDefault();
    const source = new FormData(e.target).get('source');
    try {
      if (id) await api(`/api/skills/${encodeURIComponent(id)}`, { method: 'PUT', body: { source } });
      else await api('/api/skills', { method: 'POST', body: { source } });
      m.close(); toast('Workflow saved.', 'success'); renderWorkflows(root);
    } catch (error) { toastError(error); }
  };
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
export async function renderSettings(root) {
  const s = await api('/api/settings');
  const st = S.status || {};
  const input = (name, label, placeholder = '') => `<label>${label}<input name="${name}" value="${esc(s[name] ?? '')}" placeholder="${esc(placeholder)}"></label>`;
  const check = (name, label) => `<label class="check"><input type="checkbox" name="${name}" ${s[name] ? 'checked' : ''}> ${label}</label>`;
  const languages = [['auto', 'Same as the question'], ['english', 'English'], ['hindi', 'हिंदी'], ['hinglish', 'Hinglish'], ['marathi', 'मराठी'], ['bengali', 'বাংলা'],
    ['tamil', 'தமிழ்'], ['telugu', 'తెలుగు'], ['kannada', 'ಕನ್ನಡ'], ['malayalam', 'മലയാളം'], ['gujarati', 'ગુજરાતી'], ['punjabi', 'ਪੰਜਾਬੀ'], ['odia', 'ଓଡ଼ିଆ'], ['urdu', 'اردو'], ['assamese', 'অসমীয়া']];
  root.innerHTML = `<form class="form settings" id="settings-form">
    <h2>Settings</h2>
    <fieldset><legend>You (used in greetings, reminders and on Word drafts)</legend>
      <div class="grid2"><label>Title <input name="title" value="${esc(s.title)}"></label>${input('name', 'Name')}
      ${input('enrolment', 'Bar enrolment no.')}${input('firm', 'Chambers / firm')}
      ${input('phone', 'Phone')}${input('email', 'Email')}</div>
      ${input('address', 'Address')}${input('courts', 'Courts you appear in')}${input('practice_areas', 'Practice areas')}</fieldset>
    <fieldset><legend>Answers</legend><div class="grid2">
      <label>Style <select name="style"><option value="detailed" ${s.style === 'detailed' ? 'selected' : ''}>For lawyers (detailed)</option><option value="simple" ${s.style === 'simple' ? 'selected' : ''}>Plain words (for clients)</option></select></label>
      <label>Answer language <select name="answer_language">${languages.map(([v, l]) => `<option value="${v}" ${s.answer_language === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label></div></fieldset>
    <fieldset><legend>Reminders</legend><div class="grid2">
      <label>Morning agenda at <input type="time" name="morning_time" value="${esc(s.morning_time)}"></label>
      <label>Evening-before reminder at <input type="time" name="evening_time" value="${esc(s.evening_time)}"></label>
      <label>Minutes before a timed hearing (0 = off) <input type="number" min="0" max="600" name="remind_minutes_before" value="${esc(s.remind_minutes_before)}"></label>
      <label>Deadline countdown (days before) <input name="deadline_days" value="${esc((s.deadline_days || []).join(', '))}" placeholder="7, 3, 1"></label></div>
      ${check('daily_agenda', 'Daily agenda in the morning')}${check('remind_evening_before', 'Remind me the evening before a hearing')}
      ${check('remind_morning_of', 'Remind me on the morning of a hearing')}${check('desktop_notifications', 'Show Windows desktop notifications')}
      <div class="actions left"><button type="button" class="btn small" data-test>Send a test reminder</button>
        <a class="btn small" href="/api/calendar.ics">⬇ Export calendar (.ics) for Google / Outlook</a></div>
      <p class="muted small">Reminders appear while the app is running (start.bat). Allow notifications in your browser to get them on the desktop.</p></fieldset>
    <fieldset><legend>Files</legend>${check('keep_originals', 'Keep the original uploads in each case\'s _originals folder')}
      ${check('ocr_with_claude', 'Let Claude read scanned pages the local OCR can\'t (Hindi, handwriting) - uses your Claude plan')}
      <p class="muted small">Your workspace: <code>${esc(st.workspace || '')}</code></p></fieldset>
    <fieldset><legend>AI</legend><p>${st.ai_enabled ? `✅ AI answers on: ${esc(st.engine_label)}` : '⚠ Search-only mode: no Claude Code and no API key found.'}</p>
      <p class="muted small">Law library: ${st.acts} Acts, ${(st.sections || 0).toLocaleString('en-IN')} sections · meaning index: ${esc(st.law_index?.status || '')}.
      Change the engine or model in the <code>.env</code> file (see README).</p></fieldset>
    <div class="actions"><span class="spacer"></span><button class="btn primary">Save settings</button></div></form>`;
  $('[data-test]', root).onclick = async () => {
    if ('Notification' in window && Notification.permission === 'default') await Notification.requestPermission();
    api('/api/notifications/test', { method: 'POST' }).catch(toastError);
  };
  $('#settings-form').onsubmit = async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const data = Object.fromEntries(form.entries());
    for (const key of ['daily_agenda', 'remind_evening_before', 'remind_morning_of', 'desktop_notifications', 'keep_originals', 'ocr_with_claude']) data[key] = form.has(key);
    data.deadline_days = String(data.deadline_days || '').split(/[,\s]+/).filter(Boolean).map(Number);
    try {
      S.settings = await api('/api/settings', { method: 'PUT', body: data });
      toast('Settings saved.', 'success');
    } catch (error) { toastError(error); }
  };
}

export { niceDate };
