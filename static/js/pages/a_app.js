// One app (admin): per-app stats with drill-down, plans, licenses (search, manual key), edit, API, disable, maintenance.
import { $, api, avatar, bindDrill, confirmBox, copyText, debounce, empty, esc, fld, fmtDate, fmtMoney, fmtNum, isSuper, openModal, pager, rangeBlocks, skeleton, statusBadge, tileHtml, toast, until } from '../core.js';
import { licenseModal } from './userui.js';
import { appForm } from './a_purchase.js';
import { drillActive, drillSales } from './a_home.js';

export default async function (root, appId) {
  let app, cats = [], lq = '', lp = 1;
  root.innerHTML = skeleton(3);
  async function head() {
    [app, { items: cats }] = await Promise.all([api(`/apps/${appId}`), api('/categories')]);
    const rules = (await api('/settings')).maintenance;
    const rule = rules.find((r) => r.app_id === appId);
    root.innerHTML = `<div class="page-head"><div class="row">${avatar(app.image_url, app.name, 'lg')}<div><h1>${esc(app.name)}</h1><p>${app.seller_name ? '🚀 Seller: ' + esc(app.seller_name) + ' · ' : ''}${app.status.replace('_', ' ')}${app.disabled ? ' · <b class="neg">disabled</b>' : ''}${app.sale_off ? ' · <b class="neg">not selling</b>' : ''}</p></div></div>
      <div class="row"><a class="btn" href="#/purchase">← Back</a><button class="btn w-only" id="edit">✏️ Edit</button><button class="btn w-only ${app.sale_off ? '' : 'ok'}" id="sell" title="Show / hide this app in the bot's Purchase menu. Existing customers keep working.">🛒 Selling: ${app.sale_off ? 'OFF' : 'ON'}</button><button class="btn w-only" id="dis" title="Switch the app's API off for everyone">${app.disabled ? '▶️ Enable API' : '⏸ Disable app'}</button><button class="btn danger del-only w-only" id="del">🗑</button></div></div>
      ${app.pending_submission ? `<div class="seller-note">🕒 A seller update is waiting for review. <a href="#/seller/review/${esc(app.pending_submission)}">Open review →</a></div>` : ''}
      <div id="blocks"></div>
      <div class="split gap-top"><div class="card"><h3>🔌 API</h3><dl class="kv"><dt>Verify</dt><dd class="mono">${esc(app.endpoint)}</dd><dt>Heartbeat</dt><dd class="mono">${esc(app.heartbeat_endpoint)}</dd>${app.plan_type === 'limit' ? `<dt>Consume (debit)</dt><dd class="mono">${esc(app.consume_endpoint)}</dd><dt>Usage (balance)</dt><dd class="mono">${esc(app.usage_endpoint)}</dd>` : ''}<dt>API key</dt><dd class="mono">${esc(app.api_key || '••••••')}</dd></dl>
        <div class="row gap-top"><button class="btn sm" data-copy-text="${esc(app.endpoint)}">Copy endpoint</button>${app.plan_type === 'limit' ? `<button class="btn sm" data-copy-text="${esc(app.consume_endpoint)}">Copy consume URL</button>` : ''}<button class="btn sm" data-copy-text="${esc(app.api_key || '')}">Copy key</button><button class="btn sm danger w-only" id="regen">♻️ Regenerate key</button></div></div>
        <div class="card"><h3>🛠 Maintenance</h3><p class="muted tiny">The app API answers with your status code &amp; message while this is on.</p>
          <form id="mf" class="stack"><div class="two">${fld('Status code', `<input name="status_code" type="number" min="200" max="599" value="${rule ? rule.status_code : 503}">`)}${fld('Message', `<input name="message" maxlength="300" value="${esc(rule ? rule.message : 'Under maintenance, please try again later.')}">`)}</div>
          <div class="row between"><span class="badge ${rule && rule.enabled ? 'bad' : 'ok'}">${rule && rule.enabled ? '🛠 ON' : '🟢 off'}</span><button class="btn ${rule && rule.enabled ? 'ok' : 'danger'} w-only">${rule && rule.enabled ? 'Turn off' : 'Turn on'}</button></div></form></div></div>
      <div class="section-title"><h2>🏷️ Plans</h2></div><div id="plans" class="grid auto">${app.plans.length ? app.plans.map((p) => `<div class="card"><b>${p.kind === 'trial' ? '🎁 FREE TRIAL' : fmtMoney(p.amount)}</b> <span class="muted">/ ${esc(p.label)}</span> ${p.is_best ? '<span class="badge ok">⭐ best</span>' : ''}</div>`).join('') : empty('🏷️', 'No plans yet — add them in Plans.')}</div>
      <div class="section-title"><h2>🔑 Licenses</h2><div class="row"><label class="search"><span>🔎</span><input id="lq" type="search" placeholder="Key, name, chat id" maxlength="64"></label><button class="btn primary w-only" id="mk">＋ Manual key</button></div></div>
      <div id="lics" class="list"></div><div id="lpg"></div>`;
    $('#edit', root).onclick = () => appForm({ app, cats, onDone: () => head().then(init) });
    $('#sell', root).onclick = async () => { await api(`/apps/${appId}/selling`, { method: 'POST', body: { on: !!app.sale_off } }); toast(app.sale_off ? 'Selling resumed — the app is back in the bot' : 'Hidden from the bot — existing customers are not affected'); head().then(init); };
    $('#dis', root).onclick = async () => { await api(`/apps/${appId}/disable`, { method: 'POST', body: { disabled: !app.disabled } }); toast(app.disabled ? 'App enabled' : 'App disabled — it stops selling and the API refuses it'); head().then(init); };
    const del = $('#del', root); if (del) del.onclick = async () => { if (await confirmBox('Delete app?', 'It disappears from the shop and its API stops working. Records are kept.', 'Delete')) { await api(`/apps/${appId}`, { method: 'DELETE' }); location.hash = '#/purchase'; } };
    $('#regen', root).onclick = async () => { if (await confirmBox('Regenerate API key?', 'The old key stops working immediately — update your app.', 'Regenerate')) { const d = await api(`/apps/${appId}/regenerate-key`, { method: 'POST' }); toast('New key generated'); copyText(d.api_key); head().then(init); } };
    $('#mf', root).onsubmit = async (e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); try {
      if (rule) await api(`/maintenance/${rule.id}`, { method: 'PATCH', body: { enabled: !rule.enabled } });
      else await api('/maintenance', { method: 'POST', body: { app_id: appId, status_code: f.status_code, message: f.message, enabled: true } });
      toast('Maintenance updated'); head().then(init); } catch (er) { toast(er.message, 'err'); } };
    $('#mk', root).onclick = manual;
    $('#lq', root).addEventListener('input', debounce(() => { lq = $('#lq', root).value.trim(); lp = 1; lics(); }));
  }
  async function lics() {
    try {
      const d = await api(`/apps/${appId}/licenses`, { params: { q: lq, page: lp } });
      $('#lics', root).innerHTML = d.items.length ? d.items.map((l, i) => `<div class="li wide" data-i="${i}" tabindex="0"><div class="avatar">🔑</div><div class="meta"><b class="mono">${esc(l.key)}</b>
        <small>${esc(l.full_name)} · <span class="mono">${esc(l.chat_id)}</span> · ${esc(l.validity_label)}</small></div><div class="acts">${statusBadge(l.status, l.status === 'active' ? until(l.expires_at) : '')}<small class="muted">${fmtMoney(l.amount)}</small></div></div>`).join('') : empty('🔑', 'No licenses yet.');
      $('#lics', root).onclick = (e) => { const r = e.target.closest('[data-i]'); if (r) licenseModal(d.items[+r.dataset.i], lics); };
      pager($('#lpg', root), d, (p) => { lp = p; lics(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  function manual() {
    openModal({ title: 'Create license manually', body: `<div class="two">${fld('Full name *', '<input name="full_name" required maxlength="80" placeholder="e.g. Rahul Sharma">')}${fld('Telegram chat ID *', '<input name="chat_id" required inputmode="numeric" placeholder="e.g. 123456789">')}</div>
      <div class="two">${fld('@username (optional)', '<input name="username" maxlength="40" placeholder="e.g. rahul_k (without @)">')}${fld('Amount paid ₹ * (0 = free)', '<input name="amount" type="number" min="0" step="0.01" required placeholder="e.g. 299" value="0">')}</div>
      ${app.plan_type === 'limit'
        ? `${fld('Requests *', '<input name="requests" type="number" min="1" step="1" required placeholder="e.g. 1000">')}<div class="two">${fld('Expiry (optional)', '<input name="validity_value" type="number" min="0" placeholder="blank = never expires">')}${fld('Expiry unit', '<select name="validity_unit"><option>hours</option><option selected>days</option><option>years</option></select>')}</div>`
        : `<div class="two">${fld('Validity *', '<input name="validity_value" type="number" min="1" value="30" required placeholder="e.g. 30">')}${fld('Unit *', '<select name="validity_unit"><option>hours</option><option selected>days</option><option>years</option></select>')}</div>`}`,
      onSubmit: async (d) => { const l = await api(`/apps/${appId}/licenses`, { method: 'POST', body: d }); toast('License created 🎉'); copyText(l.key); lics(); } });
  }
  async function init() {
    await rangeBlocks($('#blocks', root), {
      fetch: (start, end) => api('/stats', { params: { app_id: appId, start, end } }),
      build: (d) => [{ title: '👥 Active users', key: 'active', data: d.active, fmt: fmtNum }, { title: '💸 Earnings', key: 'earning', data: d.earning, fmt: fmtMoney },
        { title: '🛒 Purchases', key: 'sales', data: d.sales, fmt: fmtNum }],
      onDrill: (key, range, rng) => (key === 'active' ? drillActive(range, rng, appId) : drillSales(range, rng, appId, `💸 ${app.name} · sales`)),
    });
    lics();
  }
  await head(); await init();
}
