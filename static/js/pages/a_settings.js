// Admin settings: Bot & welcome, Maintenance, Force join, Messages, Wallet, Admins & security.
import { $, api, confirmBox, empty, esc, fld, fmtDate, fmtNum, isSuper, openModal, rangeBar, richEditor, skeleton, state, tabsBar, toast, copyText } from '../core.js';

export default async function (root) {
  let tab = 'bot';
  root.innerHTML = `<div class="page-head"><div><h1>⚙️ Settings</h1><p>Bot, maintenance, force-join channels, messages, wallet and admins.</p></div></div><div id="tb"></div><div id="main" class="gap-top">${skeleton(3)}</div>`;
  const TABS = [['bot', '🤖 Bot'], ['features', '🎛 Features'], ['maint', '🛠 Maintenance'], ['join', '📢 Force join'], ['msgs', '💬 Messages'], ['wallet', '👛 Wallet'], ['admins', '🛡 Admins & security']];
  $('#tb', root).appendChild(tabsBar(TABS, tab, (k) => { tab = k; draw(); }).el);
  const box = () => $('#main', root);
  const save = async (fn, ok = 'Saved ✨') => { try { await fn(); toast(ok); } catch (e) { toast(e.message, 'err'); } };

  async function draw() {
    box().innerHTML = skeleton(3);
    const s = await api('/settings');
    ({ bot, features, maint, join, msgs, wallet, admins }[tab])(s);
  }

  function bot(s) {
    const b = s.bot;
    box().innerHTML = `<div class="card stack"><h3>🤖 Welcome message</h3><div id="wl"></div>
      <div class="two">${fld('Support @username', `<input id="sup" maxlength="64" value="${esc(b.support_username)}" placeholder="support_user">`)}${fld('Admin chat ID (super admin)', `<input id="adm" inputmode="numeric" value="${esc(b.admin_chat_id)}">`, 'Gets payment alerts, review alerts and the admin commands /help /send /broadcast /pending.')}</div>
      ${fld('2nd admin chat ID', `<input id="adm2" inputmode="numeric" value="${esc(b.admin2_chat_id)}">`, 'The 2nd admin also receives every alert and can use the same commands.')}
      <div class="row"><button class="btn primary w-only" id="sv">Save</button><button class="btn" id="tw">🧪 Send test welcome</button></div></div>
      <div class="card stack gap-top"><h3>🔗 Telegram webhook</h3><p class="muted tiny">Host: <span class="mono">${esc(s.webhook_url_host)}</span> · bot token ${s.bot_configured ? '✅ set' : '❌ missing (BOT_TOKEN env)'}</p><div class="row"><button class="btn w-only" id="wh">Register webhook</button></div><p class="tiny muted">Placeholders in the welcome message: {first_name} {full_name} {username} {chat_id}</p></div>`;
    const ed = richEditor({ text: b.welcome_message, format: b.welcome_format, image: b.welcome_image, label: 'Welcome message (shown on /start)' });
    $('#wl', root).appendChild(ed.el);
    $('#sv', root).onclick = () => save(() => { const v = ed.value(); return api('/settings/bot', { method: 'PUT', body: { welcome_message: v.text, welcome_format: v.format, welcome_image: v.image, support_username: $('#sup', root).value, admin_chat_id: $('#adm', root).value, admin2_chat_id: $('#adm2', root).value } }); });
    $('#tw', root).onclick = () => save(() => api('/settings/bot/test-welcome', { method: 'POST' }), 'Test sent to the admin chat');
    $('#wh', root).onclick = () => save(async () => { const d = await api('/settings/webhook', { method: 'POST' }); if (d.last_error) throw new Error(d.last_error); }, 'Webhook registered ✅');
  }

  function features(s) {
    const F = [['referral', '🎁 Refer & Earn', 'Users can register as referrers, share their link and earn commission. Off: the Refer button disappears and no new commission is paid.'],
      ['reseller_signup', '🏪 Reseller sign-ups', 'Users can apply to become resellers. Off: the Reseller button and new applications are closed (existing resellers keep working).'],
      ['seller_signup', '🚀 Seller sign-ups', 'Users can tap “Become Seller” in the bot to get a seller account. Off: the button is hidden (existing sellers keep working).']];
    const f = s.features;
    box().innerHTML = `<div class="card stack"><h3>🎛 Feature switches</h3><p class="muted tiny">Turn whole parts of the bot on or off instantly.</p>
      ${F.map(([k, t, hint]) => `<label class="row between feat"><span><b>${t}</b><br><small class="muted">${hint}</small></span><span class="switch"><input type="checkbox" data-feat="${k}" ${f[k] ? 'checked' : ''}><i></i></span></label>`).join('')}</div>`;
    box().onchange = (e) => {
      const t = e.target.closest('[data-feat]'); if (!t) return;
      f[t.dataset.feat] = t.checked;
      save(() => api('/settings/features', { method: 'PUT', body: f }), t.checked ? 'Turned ON' : 'Turned OFF').catch(() => {});
    };
  }

  function maint(s) {
    box().innerHTML = `<div class="row between"><p class="muted">When a rule is ON, the app API answers with your status code and message instead of verifying keys.</p><button class="btn primary w-only" id="add">＋ New rule</button></div>
      <div class="list gap-top">${s.maintenance.length ? s.maintenance.map((r) => `<div class="li wide"><div class="avatar">🛠</div><div class="meta"><b>${esc(r.app_name)}</b><small>HTTP ${r.status_code} · ${esc(r.message)}${r.by === 'seller' ? ' · by seller' : ''}</small></div>
        <div class="acts"><label class="switch"><input type="checkbox" data-tg="${esc(r.id)}" ${r.enabled ? 'checked' : ''}><i></i></label><button class="btn sm w-only" data-ed="${esc(r.id)}">✏️</button><button class="btn sm danger w-only del-only" data-rm="${esc(r.id)}">🗑</button></div></div>`).join('') : empty('🛠', 'No maintenance rules.')}</div>`;
    const form = (r = {}) => openModal({ title: r.id ? 'Edit rule' : 'New maintenance rule', body: `${fld('Applies to', `<select name="app_id"><option value="all" ${r.app_id === 'all' ? 'selected' : ''}>All apps</option>${s.apps.map((a) => `<option value="${esc(a.id)}" ${a.id === r.app_id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select>`)}
      <div class="two">${fld('Status code', `<input name="status_code" type="number" min="200" max="599" value="${r.status_code || 503}" required>`)}${fld('Message', `<input name="message" maxlength="300" required value="${esc(r.message || 'Under maintenance, please try again later.')}">`)}</div>
      <label class="row"><span class="switch"><input type="checkbox" name="enabled" ${r.enabled !== false ? 'checked' : ''}><i></i></span> Enabled</label>`,
      onSubmit: async (v, f) => { const body = { ...v, enabled: f.elements.enabled.checked }; await api(r.id ? `/maintenance/${r.id}` : '/maintenance', { method: r.id ? 'PATCH' : 'POST', body }); toast('Saved ✨'); draw(); } });
    $('#add', root).onclick = () => form();
    box().onclick = async (e) => { const ed = e.target.closest('[data-ed]'), rm = e.target.closest('[data-rm]'); if (ed) form(s.maintenance.find((r) => r.id === ed.dataset.ed));
      if (rm && await confirmBox('Delete rule?', 'The app goes back to normal.', 'Delete')) { await api(`/maintenance/${rm.dataset.rm}`, { method: 'DELETE' }); draw(); } };
    box().onchange = async (e) => { const t = e.target.closest('[data-tg]'); if (t) save(() => api(`/maintenance/${t.dataset.tg}`, { method: 'PATCH', body: { enabled: t.checked } }), t.checked ? 'Maintenance ON' : 'Maintenance off'); };
  }

  async function join() {
    let rng = { range: 'all', start: '', end: '' };
    box().innerHTML = `<div class="row between"><p class="muted">Users must join these channels before using the bot. Counts include only <b>successful joins</b> (verified membership).</p><div class="row"><div id="rb"></div><button class="btn primary w-only" id="add">＋ Add channel</button></div></div><div id="cl" class="list gap-top"></div>`;
    $('#rb', root).appendChild(rangeBar((r) => { rng = r; load(); }, { initial: 'all' }).el);
    async function load() {
      const d = await api('/channels', { params: rng });
      $('#cl', root).innerHTML = d.items.length ? d.items.map((c) => `<div class="li wide"><div class="avatar">📢</div><div class="meta"><b>${esc(c.title)}</b><small>${c.username ? '@' + esc(c.username) : esc(c.chat_id)}</small>
        <div class="chips"><span class="badge ok">✅ ${fmtNum(c.joined)} joined (selected)</span><span class="badge">today ${fmtNum(c.today)}</span><span class="badge">yesterday ${fmtNum(c.yesterday)}</span><span class="badge">all ${fmtNum(c.all)}</span></div></div>
        <div class="acts"><button class="btn sm danger w-only del-only" data-rm="${esc(c.id)}">🗑</button></div></div>`).join('') : empty('📢', 'No force-join channels.');
    }
    $('#add', root).onclick = () => openModal({ title: 'Add channel', body: fld('Channel @username or ID', '<input name="chat_id" required maxlength="80" placeholder="@mychannel">', 'Add the bot as an admin of the channel first.'),
      onSubmit: async (v) => { await api('/channels', { method: 'POST', body: v }); toast('Channel added ✨'); load(); } });
    box().onclick = async (e) => { const rm = e.target.closest('[data-rm]'); if (rm && await confirmBox('Remove channel?', 'Users will no longer need to join it.', 'Remove')) { await api(`/channels/${rm.dataset.rm}`, { method: 'DELETE' }); load(); } };
    load();
  }

  function msgs(s) {
    const L = { referral: ['🎁 Refer & Earn message', 'Placeholders: {percent} {link} {count} {earned}'], reseller: ['🏪 Reseller button message', 'Shown when a user taps “Reseller” in the main bot.'],
      reseller_due: ['⛔ Reseller store maintenance message (dues)', 'Default text shown when a reseller’s bot is paused for unpaid dues (each reseller can have their own).'], reseller_panel_support: ['🛟 Reseller panel support message', 'Shown in every reseller’s Support page.'] };
    box().innerHTML = Object.keys(L).map((k) => `<div class="card stack gap-top" data-k="${k}"><h3>${L[k][0]}</h3><p class="tiny muted">${L[k][1]}</p><div class="ed"></div><div class="row"><button class="btn primary w-only" data-save>Save</button><button class="btn w-only" data-reset>Reset to default</button></div></div>`).join('');
    Object.keys(L).forEach((k) => { const card = box().querySelector(`[data-k="${k}"]`); const ed = richEditor({ ...s.messages[k], label: 'Message' }); $('.ed', card).appendChild(ed.el);
      $('[data-save]', card).onclick = () => save(() => api(`/settings/messages/${k}`, { method: 'PUT', body: ed.value() }));
      $('[data-reset]', card).onclick = async () => { if (await confirmBox('Reset message?', 'Goes back to the built-in text.', 'Reset', false)) { await api(`/settings/messages/${k}`, { method: 'DELETE' }); toast('Reset'); draw(); } }; });
  }

  function wallet(s) {
    box().innerHTML = `<div class="card stack"><h3>👛 Wallet limits</h3><form id="wf" class="two">${fld('Minimum deposit ₹', `<input name="min_deposit" type="number" min="1" value="${s.wallet.min_deposit}" required>`)}${fld('Minimum withdraw ₹ (Refer money)', `<input name="min_withdraw" type="number" min="1" value="${s.wallet.min_withdraw}" required>`)}<div><button class="btn primary w-only">Save</button></div></form></div>`;
    $('#wf', root).onsubmit = (e) => { e.preventDefault(); save(() => api('/wallet/settings', { method: 'PUT', body: Object.fromEntries(new FormData(e.target)) })); };
  }

  async function admins(s) {
    const d = await api('/admins').catch(() => ({ items: [] }));
    const me = state.me;
    box().innerHTML = `<div class="grid cols-2"><div class="card stack"><h3>🔑 Change my password</h3><form id="pf" class="stack">${fld('Current password', '<input name="current" type="password" required autocomplete="current-password">')}${fld('New password (10+ chars, letters + numbers)', '<input name="new" type="password" required minlength="10" autocomplete="new-password">')}<button class="btn primary">Change password</button></form><p class="tiny muted">Other sessions are signed out.</p></div>
      <div class="card stack"><h3>🛡 Roles</h3><p class="muted tiny"><b>Super Admin</b> — everything. <b>2nd Admin</b> — everything except deleting (login set via ADMIN2_USERNAME / ADMIN2_PASSWORD env vars${s.admin2_configured ? ' ✅' : ' — not set'}). <b>View only</b> — can see everything, change nothing.</p><p class="tiny muted">You are signed in as <b>${esc(me.username)}</b> (${esc({ admin: 'Super Admin', admin2: '2nd Admin', viewer: 'View only' }[me.role])}).</p></div></div>
      ${isSuper() ? `<div class="section-title"><h2>👥 Admin accounts</h2><button class="btn primary" id="addA">＋ View-only admin</button></div><div class="list">${d.items.map((a) => `<div class="li"><div class="avatar">${a.role === 'admin' ? '👑' : a.role === 'admin2' ? '🥈' : '👁'}</div><div class="meta"><b>${esc(a.username)}</b><small>${esc(a.role_label)} · last login ${fmtDate(a.last_login)}</small></div>
        <div class="acts">${a.role === 'viewer' ? `<label class="switch"><input type="checkbox" data-act="${esc(a.id)}" ${a.active ? 'checked' : ''}><i></i></label><button class="btn sm" data-pw="${esc(a.id)}">🔑</button><button class="btn sm danger" data-del="${esc(a.id)}">🗑</button>` : ''}</div></div>`).join('')}</div>` : ''}`;
    $('#pf', root).onsubmit = async (e) => { e.preventDefault(); save(() => api('/me/password', { method: 'POST', body: Object.fromEntries(new FormData(e.target)) }), 'Password changed ✅').then(() => e.target.reset()); };
    const add = $('#addA', root); if (add) add.onclick = () => openModal({ title: 'New view-only admin', body: `${fld('Username', '<input name="username" required maxlength="32">')}${fld('Password', '<input name="password" type="password" required minlength="10" autocomplete="new-password">')}`, onSubmit: async (v) => { await api('/admins', { method: 'POST', body: v }); toast('Created ✨'); draw(); } });
    box().onclick = async (e) => { const pw = e.target.closest('[data-pw]'), del = e.target.closest('[data-del]');
      if (pw) openModal({ title: 'Reset password', body: fld('New password', '<input name="password" type="password" required minlength="10">'), onSubmit: async (v) => { await api(`/admins/${pw.dataset.pw}`, { method: 'PATCH', body: v }); toast('Password reset'); } });
      if (del && await confirmBox('Delete admin?', 'They are signed out immediately.', 'Delete')) { await api(`/admins/${del.dataset.del}`, { method: 'DELETE' }); draw(); } };
    box().onchange = (e) => { const t = e.target.closest('[data-act]'); if (t) save(() => api(`/admins/${t.dataset.act}`, { method: 'PATCH', body: { active: t.checked } })); };
  }
  draw().catch((e) => { box().innerHTML = empty('💥', e.message); });
}
