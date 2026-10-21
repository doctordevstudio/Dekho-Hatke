// My Apps: endpoint, API key (can be revoked → new key), status Live / In verification / Rejected (+reason), per-app earnings.
import { $, api, avatar, confirmBox, copyText, empty, esc, fmtMoney, fmtNum, openModal, fld, pager, skeleton, toast } from '../core.js';

const BADGE = { live: 'ok', in_verification: 'warn', rejected: 'bad', draft: 'info' };

export default async function (root) {
  let page = 1;
  root.innerHTML = `<div class="page-head"><div><h1>📱 My Apps</h1><p>Your endpoint, API key and status. Tap an app for earnings and buyers.</p></div><div class="row"><button class="btn" id="mall">🛠 Maintenance (all apps)</button><a class="btn primary" href="#/create">＋ Create API</a></div></div><div id="list" class="grid auto">${skeleton(2)}</div><div id="pg"></div>`;
  async function load() {
    try {
      const d = await api('/apps', { params: { page } });
      $('#list', root).innerHTML = d.items.length ? d.items.map((a) => `<div class="card stack" data-id="${esc(a.id)}"><div class="row">${avatar(a.image_url, a.name, 'lg')}<div class="grow"><b>${esc(a.name)}</b><br><span class="badge ${BADGE[a.status]}">${esc(a.status_label)}</span>${a.disabled ? ' <span class="badge bad">disabled</span>' : ''}${a.maintenance ? ' <span class="badge warn">🛠 maintenance</span>' : ''}</div></div>
        ${a.reason ? `<div class="seller-note"><b>Rejected:</b> ${esc(a.reason)}</div>` : ''}
        <dl class="kv"><dt>Endpoint</dt><dd class="mono">${esc(a.endpoint)}</dd><dt>API key</dt><dd class="mono" data-key>${'•'.repeat(18)}</dd></dl>
        <div class="row"><button class="btn sm" data-copy-text="${esc(a.endpoint)}">Copy endpoint</button><button class="btn sm" data-reveal="${esc(a.api_key)}">👁 Show key</button><button class="btn sm danger" data-regen="${esc(a.id)}">♻️ Revoke key</button></div>
        <div class="app-card"><div class="stats"><div><small>Sells</small><b>${fmtNum(a.sales)}</b></div><div><small>Earned</small><b>${fmtMoney(a.earned)}</b></div></div></div>
        <div class="row"><a class="btn sm primary" href="#/apps/${esc(a.id)}">📊 Details</a><a class="btn sm" href="#/publish/${esc(a.id)}">✏️ ${a.status === 'live' ? 'Edit / update' : 'Publish'}</a>
          <button class="btn sm" data-dis="${esc(a.id)}" data-v="${a.disabled ? 0 : 1}">${a.disabled ? '▶️ Enable' : '⏸ Disable'}</button><button class="btn sm" data-m="${esc(a.id)}" data-on="${a.maintenance ? 0 : 1}">🛠 ${a.maintenance ? 'End maintenance' : 'Maintenance'}</button></div></div>`).join('') : empty('📱', 'No apps yet. Create your first API.');
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  const maint = (id, on, title) => {
    if (!on) return api(id === 'all' ? '/maintenance' : `/apps/${id}/maintenance`, { method: 'POST', body: { app_id: id, enabled: false } }).then(() => { toast('Maintenance ended'); load(); });
    openModal({ title, submit: 'Turn on', body: fld('Message shown inside the app', '<input name="message" maxlength="300" value="We are upgrading — please try again soon.">'), onSubmit: async (v) => { await api(id === 'all' ? '/maintenance' : `/apps/${id}/maintenance`, { method: 'POST', body: { app_id: id, enabled: true, message: v.message } }); toast('Maintenance ON'); load(); } });
  };
  root.addEventListener('click', async (e) => {
    if (e.target.closest('[data-copy-text]')) return;
    const rv = e.target.closest('[data-reveal]'); if (rv) { const dd = rv.closest('.card').querySelector('[data-key]'); const shown = dd.dataset.shown === '1'; dd.textContent = shown ? '•'.repeat(18) : rv.dataset.reveal; dd.dataset.shown = shown ? '0' : '1'; rv.textContent = shown ? '👁 Show key' : '🙈 Hide'; if (!shown) copyText(rv.dataset.reveal); return; }
    const rg = e.target.closest('[data-regen]'); if (rg && await confirmBox('Revoke API key?', 'The old key stops working <b>immediately</b>. Put the new key in your app.', 'Revoke & create new')) { const d = await api(`/apps/${rg.dataset.regen}/regenerate-key`, { method: 'POST' }); copyText(d.api_key); toast('New key created and copied'); load(); return; }
    const di = e.target.closest('[data-dis]'); if (di) { try { await api(`/apps/${di.dataset.dis}/disable`, { method: 'POST', body: { disabled: di.dataset.v === '1' } }); toast('Updated'); load(); } catch (er) { toast(er.message, 'err'); } return; }
    const m = e.target.closest('[data-m]'); if (m) maint(m.dataset.m, m.dataset.on === '1', 'App maintenance');
  });
  $('#mall', root).onclick = () => openModal({ title: 'Maintenance for all apps', submit: 'Apply', body: `${fld('Message', '<input name="message" maxlength="300" value="We are upgrading — please try again soon.">')}<label class="row"><span class="switch"><input type="checkbox" name="on" checked><i></i></span> Maintenance ON</label>`,
    onSubmit: async (v, f) => { await api('/maintenance', { method: 'POST', body: { app_id: 'all', enabled: f.elements.on.checked, message: v.message } }); toast('Applied'); load(); } });
  load();
}
