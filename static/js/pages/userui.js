// Shared user UI: list rows, full user details (+ purchase history, edit, wallet adjust) and the license modal.
import { api, avatar, confirmBox, copyText, esc, fld, fmtDate, fmtMoney, openModal, state, statusBadge, toast, until, pager, $, empty } from '../core.js';

const CAN_LICENSE = () => state.panel === 'admin';

export function licenseModal(l, onChange) {
  const act = CAN_LICENSE();
  const m = openModal({
    title: 'License key', noFoot: true,
    body: `<div class="row"><code>${esc(l.key)}</code><button type="button" class="btn sm" data-copy>Copy</button>${statusBadge(l.status)}</div>
      <div class="chips"><span class="badge violet">${esc(l.app_name || '—')}</span><span class="badge">${esc(l.validity_label || '')}</span>
      <span class="badge info">${l.status === 'active' ? (l.limit && l.expires_at > Date.now() / 1000 + 20 * 31536000 ? 'never expires' : until(l.expires_at)) : 'not active'}</span>
      ${l.limit ? `<span class="badge ${l.limit.remaining ? 'ok' : 'bad'}">📟 ${l.limit.remaining.toLocaleString('en-IN')} / ${l.limit.total.toLocaleString('en-IN')} requests left</span>` : ''}</div>
      <dl class="kv">
        <dt>User</dt><dd>${esc(l.full_name)} ${l.username ? '(@' + esc(l.username) + ')' : ''}</dd>
        <dt>Chat ID</dt><dd class="mono">${esc(l.chat_id)}</dd>
        <dt>Paid</dt><dd>${fmtMoney(l.amount)} · ${esc(l.source)}</dd>
        <dt>Created</dt><dd>${fmtDate(l.created_at)}</dd>
        <dt>Expires</dt><dd>${fmtDate(l.expires_at)}</dd>
        <dt>Device</dt><dd>${l.device_bound ? '🔒 linked' + (l.device_name ? ' — ' + esc(l.device_name) : '') : 'not linked yet'}</dd>
        <dt>Last seen</dt><dd>${l.last_seen ? fmtDate(l.last_seen) : 'never'}</dd>
        ${l.revoked_reason ? `<dt>Revoked</dt><dd>${esc(l.revoked_reason)}</dd>` : ''}
      </dl>
      ${act && l.limit ? `<div class="w-only"><h3>Add requests</h3><div class="row"><input name="topup" type="number" min="1" step="1" placeholder="e.g. 500" aria-label="Requests to add"><button type="button" class="btn lime" data-act="topup">➕ Add requests</button></div></div>` : ''}
      ${act ? `<div class="w-only"><h3>Renew / extend</h3>
        <div class="two"><input name="validity_value" type="number" min="1" value="30" aria-label="Amount"><select name="validity_unit" aria-label="Unit"><option value="days">Days</option><option value="hours">Hours</option><option value="years">Years</option></select></div>
        <div class="row"><button type="button" class="btn lime" data-act="renew">♻️ Renew</button>
          <button type="button" class="btn danger" data-act="revoke">⛔ Revoke</button>
          <button type="button" class="btn" data-act="expire">⏱ Expire now</button>
          <button type="button" class="btn" data-act="device">📲 Reset device</button>
          <button type="button" class="btn danger del-only" data-act="delete">🗑 Delete</button></div></div>` : ''}`,
  });
  m.el.addEventListener('click', async (e) => {
    if (e.target.closest('[data-copy]')) return copyText(l.key);
    const a = e.target.closest('[data-act]')?.dataset.act;
    if (!a) return;
    const done = (msg) => { toast(msg); m.close(); onChange && onChange(); };
    try {
      if (a === 'renew') {
        const f = new FormData(m.form);
        await api(`/licenses/${l.key}/renew`, { method: 'POST', body: { validity_value: f.get('validity_value'), validity_unit: f.get('validity_unit') } });
        done('License renewed ♻️');
      } else if (a === 'topup') {
        await api(`/licenses/${l.key}/topup`, { method: 'POST', body: { requests: new FormData(m.form).get('topup') } });
        done('Requests added ➕');
      } else if (a === 'revoke') {
        openModal({ title: 'Revoke license', submit: 'Revoke', danger: true,
          body: fld('Reason (optional)', '<input type="text" name="reason" maxlength="200" placeholder="e.g. chargeback">'),
          onSubmit: async (v) => { await api(`/licenses/${l.key}/revoke`, { method: 'POST', body: { reason: v.reason } }); done('License revoked'); } });
      } else if (a === 'expire') {
        if (await confirmBox('Expire now?', 'The key stops working immediately. You can renew it later.', 'Expire')) { await api(`/licenses/${l.key}/expire`, { method: 'POST' }); done('License expired'); }
      } else if (a === 'device') {
        if (await confirmBox('Reset device?', 'The next device that uses this key will be linked instead.', 'Reset', false)) { await api(`/licenses/${l.key}/reset-device`, { method: 'POST' }); done('Device binding reset'); }
      } else if (a === 'delete') {
        if (await confirmBox('Delete license?', 'It stops working and disappears from the panel. The record is kept in the database.', 'Delete')) { await api(`/licenses/${l.key}`, { method: 'DELETE' }); done('License deleted'); }
      }
    } catch (err) { toast(err.message, 'err'); }
  });
  return m;
}

export function userRow(u, i = 0) {
  const chips = (u.licenses || []).length
    ? u.licenses.slice(0, 4).map((l) => `<span class="chip" data-lic="${esc(l.key)}">🔑 <b>${esc(l.app_name)}</b> ${statusBadge(l.status, l.status === 'active' ? until(l.expires_at) : '')}</span>`).join('') + (u.licenses.length > 4 ? `<span class="tiny muted">+${u.licenses.length - 4} more</span>` : '')
    : '<span class="tiny muted">No license yet</span>';
  return `<div class="li" data-i="${i}" data-chat="${esc(u.chat_id)}" tabindex="0">${avatar(u.profile_image_url, u.full_name, 'lg')}
    <div class="meta"><b>${esc(u.full_name || '—')} ${u.banned ? '<span class="badge bad">🚫 blocked</span>' : ''}</b><small>${u.username ? '@' + esc(u.username) : 'no username'} · <span class="mono">${esc(u.chat_id)}</span></small>
    <div class="chips">${chips}</div></div>
    <div class="acts"><span class="badge">${u.source === 'manual' ? '✍️ manual' : '🤖 bot'}</span><span class="tiny muted">${u.joined_at ? 'joined ' + fmtDate(u.joined_at) : ''}</span></div></div>`;
}

/** Click a license chip -> license modal, click anywhere else on the row -> full user details. */
export function bindUserRows(container, users, onChange) {
  const open = (e) => {
    const chip = e.target.closest('[data-lic]');
    const row = e.target.closest('[data-chat]');
    if (chip) { for (const u of users) { const l = (u.licenses || []).find((x) => x.key === chip.dataset.lic); if (l) return licenseModal(l, onChange); } }
    else if (row) userDetail(row.dataset.chat, onChange);
  };
  container.onclick = open;
  container.onkeydown = (e) => { if (e.key === 'Enter') open(e); };
}

const bucketName = { deposit: 'Deposit', refer: 'Refer' };

export async function userDetail(chatId, onChange) {
  const m = openModal({ title: 'User details', wide: true, noFoot: true, body: '<div class="skel"></div><div class="skel"></div>' });
  const box = $('form', m.el);
  let page = 1;
  async function load() {
    let u;
    try { u = await api(`/users/${encodeURIComponent(chatId)}`); } catch (e) { box.innerHTML = empty('💥', e.message); return; }
    const per = 8, pages = Math.max(1, Math.ceil(u.purchases.length / per));
    page = Math.min(page, pages);
    const rows = u.purchases.slice((page - 1) * per, page * per);
    const sbadge = { success: 'ok', pending: 'warn', verification: 'info', failed: 'bad' };
    box.innerHTML = `<div class="row">${avatar(u.profile_image_url, u.full_name, 'lg')}<div class="grow"><h2>${esc(u.full_name || '—')}</h2>
        <small class="muted">${u.username ? '@' + esc(u.username) : 'no username'} · <span class="mono">${esc(u.chat_id)}</span> · joined ${fmtDate(u.joined_at)}</small></div>
        ${u.banned ? '<span class="badge bad">🚫 blocked</span>' : ''}</div>
      ${u.banned ? `<div class="seller-note">🚫 <b>Blocked</b> — the bot does not answer this user at all.<br><small>Reason: ${esc(u.banned.reason)} · by ${esc(u.banned.by)} · ${fmtDate(u.banned.ts)}</small></div>` : ''}
      <div class="grid cols-4 keep2 gap-top">
        <div class="tile"><div class="lbl">Spent</div><div class="val">${fmtMoney(u.totals.spent)}</div><div class="sub">${u.totals.orders} order${u.totals.orders === 1 ? '' : 's'}</div></div>
        <div class="tile"><div class="lbl">Deposit wallet</div><div class="val">${fmtMoney(u.wallet.deposit)}</div></div>
        <div class="tile"><div class="lbl">Refer wallet</div><div class="val">${fmtMoney(u.wallet.refer)}</div></div>
        <div class="tile"><div class="lbl">Referral</div><div class="val">${u.referral.percent}%</div><div class="sub">${u.referral.count || 0} referred · ${fmtMoney(u.referral.earned || 0)} earned</div></div></div>
      ${u.referred_by ? `<p class="tiny muted">Referred by <b>${esc(u.referred_by.full_name || u.referred_by.chat_id)}</b></p>` : ''}
      <div class="section-title"><h3>🔑 Licenses</h3></div>
      <div class="chips">${u.licenses.length ? u.licenses.map((l) => `<span class="chip" data-lic="${esc(l.key)}">🔑 <b>${esc(l.app_name)}</b> <span class="mono">${esc(l.key)}</span> ${statusBadge(l.status, l.status === 'active' ? until(l.expires_at) : '')}</span>`).join('') : '<span class="tiny muted">none</span>'}</div>
      <div class="section-title"><h3>🧾 Purchase history</h3></div>
      <div class="list">${rows.length ? rows.map((p) => `<div class="li wide"><div class="avatar sm">${p.kind === 'deposit' ? '👛' : '🛒'}</div>
        <div class="meta"><b>${p.kind === 'deposit' ? 'Wallet deposit' : esc(p.app_name) + ' · ' + esc(p.plan_label)}</b>
        <small>${fmtDate(p.created_at)} · <span class="mono">${esc(p.id)}</span>${p.license_key ? ' · <span class="mono">' + esc(p.license_key) + '</span>' : ''}${p.fail_reason ? ' · ' + esc(p.fail_reason) : ''}</small></div>
        <div class="acts"><b>${fmtMoney(p.amount)}${p.wallet_used ? ` <span class="tiny muted">+ ${fmtMoney(p.wallet_used)} wallet</span>` : ''}</b>
        <span class="badge ${sbadge[p.status] || ''}">${esc(p.status)}</span>${p.via && p.via !== 'upi' ? `<span class="badge violet">${esc(p.via)}</span>` : ''}</div></div>`).join('') : empty('🧾', 'No purchases yet.')}</div>
      <div id="uPg"></div>
      <div class="w-only"><div class="section-title"><h3>✏️ Edit user</h3></div>
      <div class="two">${fld('Full name', `<input type="text" id="e_name" maxlength="80" value="${esc(u.full_name)}">`)}${fld('Username (no @)', `<input type="text" id="e_user" maxlength="33" value="${esc(u.username)}">`)}</div>
      ${fld('Profile image URL', `<input type="url" id="e_img" maxlength="500" value="${esc(u.profile_image_url)}">`)}
      <div class="two">${fld('Joined on', `<input type="date" id="e_joined" value="${u.joined_at ? new Date(u.joined_at * 1000).toISOString().slice(0, 10) : ''}">`)}
        ${fld('Referred by (chat id, blank = nobody)', `<input type="text" id="e_refby" inputmode="numeric" maxlength="20" value="${esc(u.referred_by ? u.referred_by.chat_id : '')}">`)}</div>
      ${fld('Note', `<input type="text" id="e_note" maxlength="400" value="${esc(u.note || '')}">`)}
      <div class="two">${fld('Referral commission % (blank = default ' + u.referral.default_percent + '%)', `<input type="number" id="e_pct" min="0" max="90" step="0.5" value="${u.referral.custom_percent ?? ''}">`)}
        <div class="field" ${state.panel === 'admin' ? '' : 'hidden'}><span>Block user</span>${u.banned ? '<button type="button" class="btn ok" id="unban">✅ Unblock user</button>' : '<button type="button" class="btn danger" id="ban">🚫 Block user…</button>'}</div></div>
      <button type="button" class="btn primary" id="saveUser">💾 Save changes</button>
      <div class="section-title"><h3>👛 Adjust wallet</h3></div>
      <div class="row"><select id="w_bucket" aria-label="Wallet"><option value="deposit">Deposit</option><option value="refer">Refer</option></select>
        <input type="number" id="w_amt" step="1" placeholder="+100 or -50" aria-label="Amount"><input type="text" id="w_reason" maxlength="120" class="grow" placeholder="Reason (required)">
        <button type="button" class="btn" id="saveWallet">Apply</button></div></div>`;
    pager($('#uPg', box), { page, pages, total: u.purchases.length }, (p) => { page = p; load(); });
    box.onclick = async (e) => {
      const chip = e.target.closest('[data-lic]');
      if (chip) { const l = u.licenses.find((x) => x.key === chip.dataset.lic); if (l) licenseModal(l, () => { load(); onChange && onChange(); }); return; }
      try {
        if (e.target.closest('#saveUser')) {
          const body = { full_name: $('#e_name', box).value, username: $('#e_user', box).value, profile_image_url: $('#e_img', box).value,
            note: $('#e_note', box).value, referral_percent: $('#e_pct', box).value, joined_at: $('#e_joined', box).value, referred_by: $('#e_refby', box).value };
          await api(`/users/${encodeURIComponent(chatId)}`, { method: 'PATCH', body });
          toast('User updated ✨'); onChange && onChange(); load();
        } else if (e.target.closest('#ban')) {
          openModal({ title: 'Block this user?', submit: 'Block user', danger: true,
            body: `<p class="muted">The bot will <b>stop replying</b> to this user completely (main bot and reseller bots). Their licenses keep working.</p>${fld('Reason (required)', '<input name="reason" required maxlength="300" placeholder="e.g. payment fraud">')}`,
            onSubmit: async (v) => { await api(`/users/${encodeURIComponent(chatId)}/ban`, { method: 'POST', body: { reason: v.reason } }); toast('User blocked 🚫'); onChange && onChange(); load(); } });
        } else if (e.target.closest('#unban')) {
          if (await confirmBox('Unblock user?', 'The bot will answer them again.', 'Unblock', false)) { await api(`/users/${encodeURIComponent(chatId)}/unban`, { method: 'POST' }); toast('User unblocked ✅'); onChange && onChange(); load(); }
        } else if (e.target.closest('#saveWallet')) {
          await api(`/users/${encodeURIComponent(chatId)}/wallet`, { method: 'POST', body: { bucket: $('#w_bucket', box).value, amount: $('#w_amt', box).value, reason: $('#w_reason', box).value } });
          toast('Wallet updated 👛'); onChange && onChange(); load();
        }
      } catch (err) { toast(err.message, 'err'); }
    };
  }
  load();
  return m;
}
