// side.js - the left column (cases, general chats, tools) and the right column
// (calendar, next hearing, upcoming dates, reminders), plus live updates from the server.

import {
  $, $$, KIND_LABEL, S, api, confirmBox, daysUntil, esc, h, isoOf, modal, niceDate, parseISO, relativeDay,
  timeAgo, toast, toastError, todayISO,
} from './core.js';

// ---------------------------------------------------------------------------
// Left column
// ---------------------------------------------------------------------------
let caseFilter = 'active';

export async function loadSidebar() {
  const query = $('#case-search').value.trim();
  const [cases, chats] = await Promise.all([
    api(`/api/cases?status=${caseFilter === 'all' ? '' : caseFilter}&q=${encodeURIComponent(query)}`),
    api('/api/chats'),
  ]);
  S.cases = cases;
  S.generalChats = chats;
  renderSidebar();
}

export function renderSidebar() {
  const route = S.route;
  $('#case-list').innerHTML = S.cases.length ? S.cases.map((c) => {
    const next = c.next_event;
    const soon = next ? `<span class="next ${daysUntil(next.date) <= 1 ? 'urgent' : ''}">${esc(relativeDay(next.date))}</span>` : '';
    return `<li><a href="#/case/${c.id}" class="${route.caseId === c.id ? 'active' : ''} ${c.status === 'closed' ? 'closed' : ''}">
      <span class="dot" aria-hidden="true"></span><span class="name">${esc(c.title)}</span>${soon}
      <span class="sub">${esc([c.court, c.case_number].filter(Boolean).join(' · ') || c.client || 'No details yet')}</span></a></li>`;
  }).join('') : `<li class="empty">${$('#case-search').value ? 'No case matches.' : 'No cases yet. Click “New case”.'}</li>`;
  $('#chat-list').innerHTML = S.generalChats.length ? S.generalChats.slice(0, 40).map((c) => `
    <li><a href="#/chat/${c.id}" class="${route.view === 'chat' && route.chatId === c.id && !route.caseId ? 'active' : ''}">
      <span class="name">💬 ${esc(c.title)}</span><span class="sub">${esc(timeAgo(c.updated_at))}</span></a></li>`).join('')
    : '<li class="empty">No general chats yet.</li>';
  $$('#tool-links a').forEach((a) => a.classList.toggle('active', a.dataset.view === route.view));
}

export function newCaseDialog(prefill = {}) {
  const m = modal('New case', `
    <form class="form" id="new-case-form">
      <label>Case title <input name="title" required maxlength="200" placeholder="e.g. State v. Ramesh Kumar" value="${esc(prefill.title || '')}"></label>
      <div class="grid2">
        <label>Client <input name="client" placeholder="Your client"></label>
        <label>Other side <input name="opponent" placeholder="Opposite party"></label>
        <label>Court <input name="court" placeholder="e.g. Saket District Court, Delhi"></label>
        <label>Case number <input name="case_number" placeholder="e.g. Bail Appln. 1234/2026"></label>
        <label>Type <select name="case_type"><option value="">-</option><option>Criminal</option><option>Civil</option><option>Family</option>
          <option>Writ</option><option>Consumer</option><option>Commercial</option><option>Arbitration</option><option>Labour</option><option>Tax</option><option>Other</option></select></label>
        <label>CNR (eCourts) <input name="cnr" maxlength="20" placeholder="16 characters, optional"></label>
      </div>
      <p class="muted small">Only the title is needed. Drop the case files into its chat next - the AI will fill in the rest and propose how to file them.</p>
      <div class="actions"><button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary">Create case</button></div>
    </form>`);
  $('[data-cancel]', m.root).onclick = m.close;
  $('#new-case-form').onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    try {
      const result = await api('/api/cases', { method: 'POST', body: data });
      m.close();
      await loadSidebar();
      location.hash = `#/case/${result.case.id}/chat/${result.chats[0].id}`;
      toast('Case created. Drop its files into the chat to organise them.', 'success');
    } catch (error) { toastError(error); }
  };
}

export async function newGeneralChat() {
  const chat = await api('/api/chats', { method: 'POST', body: { title: 'New chat' } });
  await loadSidebar();
  location.hash = `#/chat/${chat.id}`;
}

export function initSidebar() {
  $('#new-case').onclick = () => newCaseDialog();
  $('#new-chat').onclick = () => newGeneralChat().catch(toastError);
  let timer;
  $('#case-search').oninput = () => { clearTimeout(timer); timer = setTimeout(() => loadSidebar().catch(toastError), 200); };
  $$('#case-filter button').forEach((b) => {
    b.onclick = () => {
      caseFilter = b.dataset.filter;
      $$('#case-filter button').forEach((x) => x.classList.toggle('active', x === b));
      loadSidebar().catch(toastError);
    };
  });
  $('#menu-toggle').onclick = () => document.body.classList.toggle('show-left');
  $('#agenda-toggle').onclick = () => document.body.classList.toggle('show-right');
}

// ---------------------------------------------------------------------------
// Right column: calendar, next hearing, upcoming, reminders
// ---------------------------------------------------------------------------
let shownMonth = (() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); })();
let selectedDay = todayISO();
let monthEvents = [];

export async function loadAgenda() {
  const start = new Date(shownMonth.getFullYear(), shownMonth.getMonth(), 1);
  const gridStart = new Date(start); gridStart.setDate(1 - ((start.getDay() + 6) % 7));
  const gridEnd = new Date(gridStart); gridEnd.setDate(gridStart.getDate() + 41);
  const [agenda, events, notes] = await Promise.all([
    api('/api/agenda?days=14'),
    api(`/api/events?start=${isoOf(gridStart)}&end=${isoOf(gridEnd)}`),
    api('/api/notifications?limit=30'),
  ]);
  monthEvents = events;
  renderCalendar(gridStart);
  renderAgenda(agenda);
  renderNotifications(notes);
}

function renderCalendar(gridStart) {
  const monthName = shownMonth.toLocaleString('en-IN', { month: 'long', year: 'numeric' });
  const byDay = {};
  for (const e of monthEvents) (byDay[e.date] = byDay[e.date] || []).push(e);
  const cells = [];
  for (let i = 0; i < 42; i += 1) {
    const day = new Date(gridStart); day.setDate(gridStart.getDate() + i);
    const iso = isoOf(day);
    const items = (byDay[iso] || []).filter((e) => e.status !== 'cancelled');
    const dots = [...new Set(items.map((e) => e.kind))].slice(0, 3).map((k) => `<i class="k-${k}"></i>`).join('');
    cells.push(`<button class="day ${day.getMonth() !== shownMonth.getMonth() ? 'other' : ''} ${iso === todayISO() ? 'today' : ''} ${iso === selectedDay ? 'selected' : ''}"
      data-day="${iso}" aria-label="${niceDate(iso)}${items.length ? `, ${items.length} item${items.length > 1 ? 's' : ''}` : ''}">${day.getDate()}<span class="dots">${dots}</span></button>`);
  }
  $('#calendar').innerHTML = `
    <div class="cal-head"><button class="icon-btn" data-move="-1" aria-label="Previous month">‹</button>
      <strong>${esc(monthName)}</strong><button class="icon-btn" data-move="1" aria-label="Next month">›</button></div>
    <div class="cal-grid">${['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((d) => `<span class="wd">${d}</span>`).join('')}${cells.join('')}</div>
    <div class="day-list"></div>`;
  $$('#calendar [data-move]').forEach((b) => {
    b.onclick = () => { shownMonth = new Date(shownMonth.getFullYear(), shownMonth.getMonth() + Number(b.dataset.move), 1); loadAgenda().catch(toastError); };
  });
  $$('#calendar .day').forEach((b) => { b.onclick = () => { selectedDay = b.dataset.day; renderCalendar(gridStart); }; });
  const items = (byDay[selectedDay] || []);
  $('#calendar .day-list').innerHTML = `<div class="day-list-head">${esc(niceDate(selectedDay))}
      <button class="link small" data-add>+ Add</button></div>` +
    (items.length ? items.map(eventRow).join('') : '<p class="muted small">Nothing on this day.</p>');
  $('#calendar [data-add]').onclick = () => eventDialog({ date: selectedDay });
  bindEventRows($('#calendar'));
}

function eventRow(e) {
  return `<div class="event-row k-${e.kind} ${e.status !== 'open' ? 'done' : ''}" data-event="${e.id}">
    <span class="when">${e.time ? esc(e.time) : 'All day'}</span>
    <span class="what"><b>${esc(e.title)}</b>${e.case_title ? `<br><a href="#/case/${e.case_id}" class="muted small">${esc(e.case_title)}</a>` : ''}
    ${e.location ? `<br><span class="muted small">${esc(e.location)}</span>` : ''}</span>
    <span class="kind">${esc(KIND_LABEL[e.kind] || e.kind)}</span></div>`;
}

function bindEventRows(root) {
  $$('[data-event]', root).forEach((row) => {
    row.onclick = (event) => {
      if (event.target.closest('a')) return;
      const found = monthEvents.find((e) => e.id === Number(row.dataset.event)) || lastAgendaEvents.find((e) => e.id === Number(row.dataset.event));
      if (found) eventDialog(found);
    };
  });
}

let lastAgendaEvents = [];
function renderAgenda(agenda) {
  lastAgendaEvents = [...agenda.upcoming, ...agenda.overdue, ...(agenda.next_hearing ? [agenda.next_hearing] : [])];
  const next = agenda.next_hearing;
  const name = S.settings?.display_name;
  $('#next-hearing').innerHTML = next ? `
    <div class="nh-label">Next hearing</div>
    <div class="nh-date">${esc(relativeDay(next.date))}${next.time ? ` · ${esc(next.time)}` : ''}</div>
    <div class="nh-full muted small">${esc(niceDate(next.date))}</div>
    ${next.case_title ? `<a class="nh-case" href="#/case/${next.case_id}">${esc(next.case_title)}</a>` : ''}
    <div class="nh-title">${esc(next.title)}</div>
    ${next.location || next.case_court ? `<div class="muted small">📍 ${esc(next.location || next.case_court)}</div>` : ''}
    ${next.case_id ? `<button class="btn small primary" data-brief>Prepare hearing brief</button>` : ''}`
    : `<div class="nh-label">Next hearing</div><p class="muted small">No hearings in your calendar${name ? `, ${esc(name)}` : ''}. Dates found in case files are added when you approve their plan.</p>`;
  const brief = $('#next-hearing [data-brief]');
  if (brief) brief.onclick = () => import('./chat.js').then((m) => m.startHearingBrief(next));

  const groups = {};
  for (const e of agenda.upcoming) (groups[e.date] = groups[e.date] || []).push(e);
  $('#upcoming').innerHTML = `<h3>Next 14 days</h3>` + (Object.keys(groups).length
    ? Object.entries(groups).map(([day, items]) => `<div class="up-day"><div class="up-date">${esc(relativeDay(day))} <span class="muted small">${esc(niceDate(day, false))}</span></div>${items.map(eventRow).join('')}</div>`).join('')
    : '<p class="muted small">Nothing in the next two weeks.</p>')
    + (agenda.overdue.length ? `<h3 class="overdue-head">Overdue</h3>${agenda.overdue.map(eventRow).join('')}` : '');
  bindEventRows($('#upcoming'));
}

function renderNotifications(data) {
  const badge = $('#bell-count');
  badge.textContent = data.unread > 99 ? '99+' : String(data.unread);
  badge.hidden = !data.unread;
  $('#notifications').innerHTML = data.items.length ? data.items.map((n) => `
    <div class="note ${n.read_at ? '' : 'unread'} n-${n.kind}" data-note="${n.id}" data-case="${n.case_id || ''}">
      <div class="note-title">${esc(n.title)}</div>
      ${n.body ? `<div class="note-body">${esc(n.body).replace(/\n/g, '<br>')}</div>` : ''}
      <div class="muted small">${esc(timeAgo(n.created_at))}</div></div>`).join('')
    : '<p class="muted small">No reminders yet. They appear here before your hearings and deadlines.</p>';
  $$('#notifications [data-note]').forEach((el) => {
    el.onclick = async () => {
      await api('/api/notifications/read', { method: 'POST', body: { ids: [Number(el.dataset.note)] } }).catch(() => {});
      if (el.dataset.case) location.hash = `#/case/${el.dataset.case}`;
      loadAgenda().catch(() => {});
    };
  });
}

export function eventDialog(event = {}) {
  const editing = Boolean(event.id);
  const caseOptions = S.cases.map((c) => `<option value="${c.id}" ${c.id === event.case_id ? 'selected' : ''}>${esc(c.title)}</option>`).join('');
  const m = modal(editing ? 'Calendar entry' : 'Add to calendar', `
    <form class="form" id="event-form">
      <label>What <input name="title" required maxlength="200" value="${esc(event.title || '')}" placeholder="e.g. Arguments on bail application"></label>
      <div class="grid2">
        <label>Kind <select name="kind">${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}" ${k === (event.kind || 'hearing') ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
        <label>Case <select name="case_id"><option value="">(none)</option>${caseOptions}</select></label>
        <label>Date <input type="date" name="date" required value="${esc(event.date || todayISO())}"></label>
        <label>Time <input type="time" name="time" value="${esc(event.time || '')}"></label>
      </div>
      <label>Where <input name="location" maxlength="300" value="${esc(event.location || '')}" placeholder="Court / court room"></label>
      <label>Notes <textarea name="notes" rows="2">${esc(event.notes || '')}</textarea></label>
      ${event.quote ? `<p class="quote small">From ${event.document_id ? `<button type="button" class="chip doc verified" data-doc="${event.document_id}" data-page="${event.page || ''}">📄 ${esc(event.document_title || 'the document')}${event.page ? `, p. ${event.page}` : ''}</button>` : 'a document'}: “${esc(event.quote)}”</p>` : ''}
      <div class="actions">
        ${editing ? `<button type="button" class="btn danger" data-delete>Delete</button>
          <button type="button" class="btn" data-done>${event.status === 'done' ? 'Mark not done' : 'Mark done'}</button>` : ''}
        <span class="spacer"></span>
        <button type="button" class="btn" data-cancel>Cancel</button><button class="btn primary">Save</button>
      </div>
    </form>`);
  $('[data-cancel]', m.root).onclick = m.close;
  $('#event-form').onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    data.case_id = data.case_id ? Number(data.case_id) : null;
    try {
      if (editing) await api(`/api/events/${event.id}`, { method: 'PATCH', body: data });
      else await api('/api/events', { method: 'POST', body: data });
      m.close();
      toast('Calendar updated.', 'success');
      refreshAfterCalendarChange();
    } catch (error) { toastError(error); }
  };
  if (editing) {
    $('[data-delete]', m.root).onclick = async () => {
      if (!(await confirmBox('Delete this calendar entry?', 'Delete'))) return;
      await api(`/api/events/${event.id}`, { method: 'DELETE' }).catch(toastError);
      m.close(); refreshAfterCalendarChange();
    };
    $('[data-done]', m.root).onclick = async () => {
      await api(`/api/events/${event.id}`, { method: 'PATCH', body: { status: event.status === 'done' ? 'open' : 'done' } }).catch(toastError);
      m.close(); refreshAfterCalendarChange();
    };
  }
}

function refreshAfterCalendarChange() {
  loadAgenda().catch(toastError);
  loadSidebar().catch(() => {});
  window.dispatchEvent(new CustomEvent('calendar-changed'));
}

export function initRight() {
  $('#add-event').onclick = () => eventDialog({ date: selectedDay });
  $('#mark-all-read').onclick = async () => {
    await api('/api/notifications/read', { method: 'POST', body: {} }).catch(toastError);
    loadAgenda().catch(() => {});
  };
  $('#bell').onclick = () => {
    document.body.classList.add('show-right');
    $('#notifications-box').scrollIntoView({ behavior: 'smooth' });
  };
  updateDesktopButton();
  $('#desktop-notify').onclick = async () => {
    if (!('Notification' in window)) { toast('This browser cannot show desktop notifications.', 'warn'); return; }
    const result = await Notification.requestPermission();
    updateDesktopButton();
    if (result === 'granted') api('/api/notifications/test', { method: 'POST' }).catch(() => {});
  };
}

function updateDesktopButton() {
  const button = $('#desktop-notify');
  const ok = 'Notification' in window && Notification.permission === 'granted';
  button.hidden = ok || !('Notification' in window);
}

// ---------------------------------------------------------------------------
// Live updates from the server (/api/stream)
// ---------------------------------------------------------------------------
export function connectLive(handlers) {
  const source = new EventSource('/api/stream');
  const on = (kind, fn) => source.addEventListener(kind, (e) => { try { fn(JSON.parse(e.data)); } catch (err) { console.error(err); } });
  on('notification', (data) => {
    const n = data.notification;
    toast(`🔔 ${n.title}`, 'info', 8000);
    if (S.settings?.desktop_notifications !== false && 'Notification' in window && Notification.permission === 'granted') {
      try {
        const shown = new Notification(n.title, { body: n.body || '', tag: n.key, icon: '/favicon.svg' });
        shown.onclick = () => { window.focus(); if (n.case_id) location.hash = `#/case/${n.case_id}`; };
      } catch (_) { /* some browsers need a service worker */ }
    }
    loadAgenda().catch(() => {});
  });
  for (const kind of ['calendar']) on(kind, () => { loadAgenda().catch(() => {}); loadSidebar().catch(() => {}); });
  for (const kind of ['cases', 'chats', 'case']) on(kind, () => loadSidebar().catch(() => {}));
  for (const [kind, fn] of Object.entries(handlers)) on(kind, fn);
  source.onerror = () => { /* the browser reconnects by itself */ };
  return source;
}

export { parseISO, h };
