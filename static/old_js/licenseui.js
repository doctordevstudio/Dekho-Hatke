// License detail modal (renew / revoke / reset device / delete) + user list rendering shared by Users, search and app pages.
import { api, avatar, confirmBox, copyText, esc, fmtDate, fmtMoney, openModal, statusBadge, toast, until, userCell, $ } from './core.js';

export function licenseModal(l, onChange) {
  const m = openModal({
    title: 'License key', noFoot: true,
    body: `<div class="row"><code>${esc(l.key)}</code><button type="button" class="btn sm" data-copy>Copy</button>${statusBadge(l.status)}</div>
      <div class="chips"><span class="badge violet">${esc(l.app_name || '—')}</span><span class="badge">${esc(l.validity_label || '')}</span>
      <span class="badge info">${l.status === 'active' ? until(l.expires_at) : 'not active'}</span></div>
      <dl class="kv" data-kv>
        <dt>User</dt><dd>${esc(l.full_name)} ${l.username ? '(@' + esc(l.username) + ')' : ''}</dd>
        <dt>Chat ID</dt><dd class="mono">${esc(l.chat_id)}</dd>
        <dt>Paid</dt><dd>${fmtMoney(l.amount)} · ${esc(l.source)}</dd>
        <dt>Created</dt><dd>${fmtDate(l.created_at)}</dd>
        <dt>Expires</dt><dd>${fmtDate(l.expires_at)}</dd>
        <dt>Device</dt><dd>${l.device_bound ? '🔒 linked' + (l.device_name ? ' — ' + esc(l.device_name) : '') : 'not linked yet'}</dd>
        <dt>Last seen</dt><dd>${l.last_seen ? fmtDate(l.last_seen) : 'never'}</dd>
        ${l.revoked_reason ? `<dt>Revoked</dt><dd>${esc(l.revoked_reason)}</dd>` : ''}
      </dl>
      <div class="w-only">
        <h3>Renew / extend</h3>
        <div class="two"><input name="validity_value" type="number" min="1" value="30" aria-label="Amount"><select name="validity_unit" aria-label="Unit"><option value="days">Days</option><option value="hours">Hours</option></select></div>
        <div class="row">
          <button type="button" class="btn lime" data-act="renew">♻️ Renew</button>
          <button type="button" class="btn danger" data-act="revoke">⛔ Revoke</button>
          <button type="button" class="btn" data-act="device">📲 Reset device</button>
          <button type="button" class="btn danger" data-act="delete">🗑 Delete</button>
        </div>
      </div>`,
  });
  m.el.addEventListener('click', async (e) => {
    if (e.target.closest('[data-copy]')) return copyText(l.key);
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (!act) return;
    const done = (msg) => { toast(msg); m.close(); onChange && onChange(); };
    try {
      if (act === 'renew') {
        const f = new FormData(m.form);
        await api(`/licenses/${l.key}/renew`, { method: 'POST', body: { validity_value: f.get('validity_value'), validity_unit: f.get('validity_unit') } });
        done('License renewed ♻️');
      } else if (act === 'revoke') {
        openModal({ title: 'Revoke license', submit: 'Revoke', danger: true,
          body: '<label class="field"><span>Reason (optional)</span><input type="text" name="reason" maxlength="200" placeholder="e.g. chargeback"></label>',
          onSubmit: async (v) => { await api(`/licenses/${l.key}/revoke`, { method: 'POST', body: { reason: v.reason } }); done('License revoked'); } });
      } else if (act === 'device') {
        if (await confirmBox('Reset device?', 'The next device that uses this key will be linked instead.', 'Reset', false)) {
          await api(`/licenses/${l.key}/reset-device`, { method: 'POST' }); done('Device binding reset');
        }
      } else if (act === 'delete') {
        if (await confirmBox('Delete license?', 'It disappears from the panel and stops working. The record is kept in the database.', 'Delete')) {
          await api(`/licenses/${l.key}`, { method: 'DELETE' }); done('License deleted');
        }
      }
    } catch (err) { toast(err.message, 'err'); }
  });
  return m;
}

export function userItem(u) {
  const chips = u.licenses.length
    ? u.licenses.map((l) => `<span class="chip" data-lic="${esc(l.key)}">🔑 <b>${esc(l.app_name)}</b> <span class="mono">${esc(l.key)}</span> ${statusBadge(l.status, l.status === 'active' ? until(l.expires_at) : '')}</span>`).join('')
    : '<span class="tiny muted">No license yet</span>';
  return `<div class="li">${avatar(u.profile_image_url, u.full_name, 'lg')}
    <div class="meta"><b>${esc(u.full_name || '—')}</b><small>${u.username ? '@' + esc(u.username) : 'no username'} · <span class="mono">${esc(u.chat_id)}</span></small>
    <div class="chips">${chips}</div></div>
    <div class="acts"><span class="badge">${u.source === 'manual' ? '✍️ manual' : '🤖 bot'}</span><span class="tiny muted">${u.joined_at ? 'joined ' + fmtDate(u.joined_at) : ''}</span></div></div>`;
}

export function bindUserList(container, users, onChange) {
  container.onclick = (e) => {
    const chip = e.target.closest('[data-lic]');
    if (!chip) return;
    for (const u of users) { const l = u.licenses.find((x) => x.key === chip.dataset.lic); if (l) return licenseModal(l, onChange); }
  };
}
