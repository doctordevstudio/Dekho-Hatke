import { $, $$, api, confirmBox, empty, esc, fmtDate, fmtNum, openModal, skeleton, state, toast } from './core.js';

const TABS = [['maint', '🛠 Maintenance'], ['bot', '🤖 Telegram bot'], ['channels', '📢 Force join'], ['admins', '🛡 Admins'], ['account', '🔐 My account']];

export default async function (root) {
  let tab = 'maint';
  const isAdmin = state.me.role === 'admin';
  root.innerHTML = `<div class="page-head"><div><h1>⚙️ Settings</h1><p>Maintenance, bot behaviour, channels and team access.</p></div></div>
    <div class="tabs" id="tabs">${TABS.filter(([k]) => isAdmin || k !== 'admins').map(([k, l]) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${l}</button>`).join('')}</div>
    <div id="pane" class="stack">${skeleton(3)}</div>`;
  const pane = $('#pane', root);
  const views = { maint, bot, channels, admins, account };
  async function show() { pane.innerHTML = skeleton(3); try { await views[tab](pane); } catch (e) { pane.innerHTML = empty('💥', e.message); } }
  $('#tabs', root).addEventListener('click', (e) => {
    const b = e.target.closest('[data-t]'); if (!b) return;
    tab = b.dataset.t; $$('#tabs button', root).forEach((x) => x.classList.toggle('on', x === b)); show();
  });
  show();
}

// ------------------------------------------------------------------ maintenance
async function maint(pane) {
  const d = await api('/settings');
  const form = (r) => `<label class="field"><span>Apply to</span><select name="app_id"><option value="all" ${r?.app_id === 'all' ? 'selected' : ''}>All apps</option>${d.apps.map((a) => `<option value="${esc(a.id)}" ${r?.app_id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></label>
    <label class="field"><span>Message shown to the app</span><input type="text" name="message" maxlength="300" required value="${esc(r?.message || '')}" placeholder="We’ll be back in 30 minutes"></label>
    <label class="field"><span>Response status code (e.g. 200 or 301)</span><input type="number" name="status_code" min="200" max="599" required value="${esc(r?.status_code ?? 200)}"></label>`;
  pane.innerHTML = `<div class="card"><div class="row between"><div><h3>Maintenance mode</h3><p class="muted">While a rule is ON, your app’s API calls return the status code and message you set here.</p></div>
    <button class="btn primary w-only" id="addRule">＋ Add rule</button></div></div>
    <div class="list">${d.maintenance.length ? d.maintenance.map((r) => `<div class="li"><div class="avatar">🛠</div><div class="meta"><b>${esc(r.app_name)}</b><small>HTTP ${esc(r.status_code)} · “${esc(r.message)}”</small></div>
      <div class="acts"><label class="switch w-only"><input type="checkbox" data-toggle="${esc(r.id)}" ${r.enabled ? 'checked' : ''}><i></i></label><span class="badge ${r.enabled ? 'warn' : ''}">${r.enabled ? 'ON' : 'off'}</span>
      <button class="btn sm w-only" data-edit="${esc(r.id)}">✏️</button><button class="btn sm danger w-only" data-del="${esc(r.id)}">🗑</button></div></div>`).join('') : empty('🟢', 'No maintenance rules. Everything is live.')}</div>`;
  $('#addRule', pane).addEventListener('click', () => openModal({ title: 'New maintenance rule', body: form(null), submit: 'Create',
    onSubmit: async (v) => { await api('/maintenance', { method: 'POST', body: { ...v, enabled: true } }); toast('Rule created (ON)'); maint(pane); } }));
  pane.addEventListener('click', async (e) => {
    const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]');
    if (ed) { const r = d.maintenance.find((x) => x.id === ed.dataset.edit); openModal({ title: 'Edit rule', body: form(r), onSubmit: async (v) => { await api('/maintenance/' + r.id, { method: 'PATCH', body: v }); maint(pane); } }); }
    if (del && await confirmBox('Delete rule?', 'It stops applying immediately.', 'Delete')) { await api('/maintenance/' + del.dataset.del, { method: 'DELETE' }); maint(pane); }
  });
  pane.addEventListener('change', async (e) => {
    const t = e.target.closest('[data-toggle]'); if (!t) return;
    try { await api('/maintenance/' + t.dataset.toggle, { method: 'PATCH', body: { enabled: t.checked } }); toast(t.checked ? 'Maintenance ON' : 'Maintenance OFF'); maint(pane); }
    catch (err) { toast(err.message, 'err'); t.checked = !t.checked; }
  });
}

// ------------------------------------------------------------------ telegram bot
async function bot(pane) {
  const d = await api('/settings'); const b = d.bot;
  pane.innerHTML = `<div class="card"><h3>Welcome message</h3>
    <p class="muted">Placeholders: <code>{first_name}</code> <code>{full_name}</code> <code>{username}</code> <code>{chat_id}</code>. HTML mode supports &lt;b&gt; &lt;i&gt; &lt;u&gt; &lt;code&gt; &lt;a href&gt;.</p>
    <form id="botForm"><div class="seg field"><button type="button" data-f="html" class="${b.welcome_format === 'html' ? 'on' : ''}">HTML message</button><button type="button" data-f="text" class="${b.welcome_format === 'text' ? 'on' : ''}">Plain text</button></div>
    <input type="hidden" name="welcome_format" value="${esc(b.welcome_format)}">
    <label class="field"><span>Message</span><textarea name="welcome_message" maxlength="3500" required>${esc(b.welcome_message)}</textarea></label>
    <div class="two"><label class="field"><span>Customer support bot / username</span><input type="text" name="support_username" maxlength="64" value="${esc(b.support_username)}" placeholder="@YourSupportBot"></label>
    <label class="field"><span>Admin chat ID (alerts & approvals)</span><input type="text" name="admin_chat_id" inputmode="numeric" value="${esc(b.admin_chat_id)}" placeholder="123456789"></label></div>
    <div class="row w-only"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" id="test">✈️ Send test to admin chat</button></div></form></div>
    <div class="card"><h3>Webhook</h3><p class="muted">Telegram sends updates to <code>${esc(d.webhook_url_host)}/telegram/…</code>. It is registered automatically on every start; use this if the bot stops answering.</p>
    <div class="row w-only"><button class="btn lime" id="hook" ${d.bot_configured ? '' : 'disabled'}>🔗 Re-register webhook</button>${d.bot_configured ? '' : '<span class="badge bad">BOT_TOKEN missing</span>'}</div></div>`;
  const f = $('#botForm', pane);
  $('.seg', f).addEventListener('click', (e) => { const x = e.target.closest('[data-f]'); if (!x) return; f.welcome_format.value = x.dataset.f; $$('.seg button', f).forEach((y) => y.classList.toggle('on', y === x)); });
  f.addEventListener('submit', async (e) => { e.preventDefault(); try { await api('/settings/bot', { method: 'PUT', body: Object.fromEntries(new FormData(f)) }); toast('Saved ✅'); } catch (err) { toast(err.message, 'err'); } });
  $('#test', pane).addEventListener('click', async () => { try { await api('/settings/bot', { method: 'PUT', body: Object.fromEntries(new FormData(f)) }); await api('/settings/bot/test-welcome', { method: 'POST' }); toast('Test sent to your admin chat'); } catch (err) { toast(err.message, 'err'); } });
  $('#hook', pane).addEventListener('click', async () => { try { const r = await api('/settings/webhook', { method: 'POST' }); toast(`Webhook set · ${r.pending} pending${r.last_error ? ' · last error: ' + r.last_error : ''}`); } catch (err) { toast(err.message, 'err'); } });
}

// ------------------------------------------------------------------ force join channels
async function channels(pane) {
  let rng = 'all', start = '', end = '';
  async function draw() {
    const d = await api('/channels', { params: { range: rng, start, end } });
    pane.innerHTML = `<div class="card"><div class="row between"><div><h3>Force-join channels</h3><p class="muted">Users must join every channel below before they can buy. The bot must be an admin in each channel.</p></div><button class="btn primary w-only" id="addCh">＋ Add channel</button></div>
      <div class="row"><div class="seg" id="seg"><button data-r="today" class="${rng === 'today' ? 'on' : ''}">Today</button><button data-r="yesterday" class="${rng === 'yesterday' ? 'on' : ''}">Yesterday</button><button data-r="all" class="${rng === 'all' ? 'on' : ''}">All time</button><button data-r="custom" class="${rng === 'custom' ? 'on' : ''}">Custom</button></div>
      <span ${rng === 'custom' ? '' : 'hidden'} class="row"><input type="date" id="from" value="${esc(start)}"><input type="date" id="to" value="${esc(end)}"><button class="btn sm primary" id="apply">Apply</button></span></div></div>
      <div class="grid auto">${d.items.length ? d.items.map((c) => `<div class="card"><div class="row"><div class="avatar lg">📢</div><div class="grow"><h3>${esc(c.title)}</h3><small class="muted mono">${esc(c.chat_id)}</small></div></div>
        <div class="tile hot"><div class="lbl">Users forced to join</div><div class="val">${fmtNum(c.joined)}</div><div class="sub">today ${fmtNum(c.today)} · yesterday ${fmtNum(c.yesterday)} · all ${fmtNum(c.all)}</div></div>
        <div class="row w-only"><button class="btn sm danger" data-del="${esc(c.id)}">Remove</button></div></div>`).join('') : `<div class="card">${empty('📣', 'No channels yet — users can buy without joining.')}</div>`}</div>`;
    $('#seg', pane).addEventListener('click', (e) => { const b = e.target.closest('[data-r]'); if (!b) return; rng = b.dataset.r; if (rng !== 'custom') draw(); else $$('.row[hidden]', pane).forEach((x) => { x.hidden = false; }); });
    const ap = $('#apply', pane); if (ap) ap.addEventListener('click', () => { start = $('#from', pane).value; end = $('#to', pane).value; if (!start || !end) return toast('Pick both dates', 'err'); draw(); });
    $('#addCh', pane).addEventListener('click', () => openModal({ title: 'Add channel', submit: 'Add',
      body: '<label class="field"><span>Channel @username or numeric ID</span><input type="text" name="chat_id" required placeholder="@mychannel or -1001234567890"></label><p class="tiny muted">Add the bot as an administrator first, otherwise Telegram will refuse.</p>',
      onSubmit: async (v) => { await api('/channels', { method: 'POST', body: v }); toast('Channel added 📢'); draw(); } }));
    pane.onclick = async (e) => { const del = e.target.closest('[data-del]'); if (del && await confirmBox('Remove channel?', 'Users will no longer be forced to join it. History is kept.', 'Remove')) { await api('/channels/' + del.dataset.del, { method: 'DELETE' }); draw(); } };
  }
  await draw();
}

// ------------------------------------------------------------------ admins (view-only accounts)
async function admins(pane) {
  const d = await api('/admins');
  pane.innerHTML = `<div class="card"><div class="row between"><div><h3>Team access</h3><p class="muted">Extra admins are <b>view-only</b>: they can look at everything but cannot change anything.</p></div><button class="btn primary" id="addAdm">＋ Create view-only admin</button></div></div>
    <div class="list">${d.items.map((a) => `<div class="li"><div class="avatar">${esc(a.username.charAt(0).toUpperCase())}</div><div class="meta"><b>${esc(a.username)}</b><small>created ${fmtDate(a.created_at)} by ${esc(a.created_by)} · last login ${a.last_login ? fmtDate(a.last_login) : 'never'}</small></div>
      <div class="acts"><span class="badge ${a.role === 'admin' ? 'violet' : 'info'}">${a.role === 'admin' ? 'Admin' : 'View only'}</span>${a.role === 'admin' ? '' : `<span class="badge ${a.active ? 'ok' : 'bad'}">${a.active ? 'active' : 'disabled'}</span>
      <button class="btn sm" data-pw="${esc(a.id)}">🔑 Reset password</button><button class="btn sm" data-act="${esc(a.id)}" data-on="${a.active ? 0 : 1}">${a.active ? 'Disable' : 'Enable'}</button><button class="btn sm danger" data-del="${esc(a.id)}">🗑</button>`}</div></div>`).join('')}</div>`;
  $('#addAdm', pane).addEventListener('click', () => openModal({ title: 'Create view-only admin', submit: 'Create',
    body: '<label class="field"><span>Username</span><input type="text" name="username" required maxlength="32" autocomplete="off"></label><label class="field"><span>Password (min 10, letters + numbers)</span><input type="password" name="password" required autocomplete="new-password"></label>',
    onSubmit: async (v) => { await api('/admins', { method: 'POST', body: v }); toast('Admin created'); admins(pane); } }));
  pane.onclick = async (e) => {
    const pw = e.target.closest('[data-pw]'), act = e.target.closest('[data-act]'), del = e.target.closest('[data-del]');
    if (pw) openModal({ title: 'Reset password', body: '<label class="field"><span>New password</span><input type="password" name="password" required autocomplete="new-password"></label>', onSubmit: async (v) => { await api('/admins/' + pw.dataset.pw, { method: 'PATCH', body: v }); toast('Password reset · their sessions were ended'); } });
    if (act) { await api('/admins/' + act.dataset.act, { method: 'PATCH', body: { active: act.dataset.on === '1' } }); admins(pane); }
    if (del && await confirmBox('Delete this admin?', 'They lose access immediately. The record is kept.', 'Delete')) { await api('/admins/' + del.dataset.del, { method: 'DELETE' }); admins(pane); }
  };
}

// ------------------------------------------------------------------ own account
async function account(pane) {
  pane.innerHTML = `<div class="card"><h3>Change password</h3><form id="pwForm"><label class="field"><span>Current password</span><input type="password" name="current" required autocomplete="current-password"></label>
    <label class="field"><span>New password (min 10, letters + numbers)</span><input type="password" name="new" required autocomplete="new-password"></label>
    <button class="btn primary" type="submit">Update password</button></form><p class="tiny muted">Changing it signs you out of all other devices.</p></div>`;
  $('#pwForm', pane).addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await api('/me/password', { method: 'POST', body: Object.fromEntries(new FormData(e.target)) }); toast('Password updated 🔐'); e.target.reset(); }
    catch (err) { toast(err.message, 'err'); }
  });
}
