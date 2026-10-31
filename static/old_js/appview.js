import { $, api, avatar, confirmBox, copyText, debounce, empty, esc, fmtDate, fmtMoney, fmtNum, openModal, pager, rangeBlocks, skeleton, state, statusBadge, toast, until } from './core.js';
import { licenseModal } from './licenseui.js';

const licRow = (l) => `<div class="li">${avatar(l.profile_image_url, l.full_name)}
  <div class="meta"><b>${esc(l.full_name || '—')}</b><small>${l.username ? '@' + esc(l.username) : 'no username'} · <span class="mono">${esc(l.chat_id)}</span></small>
  <div class="chips"><span class="chip mono" data-copy-text="${esc(l.key)}">🔑 ${esc(l.key)}</span></div></div>
  <div class="acts">${statusBadge(l.status, l.status === 'active' ? until(l.expires_at) : '')}<span class="badge">${esc(l.validity_label)}</span>
  <span class="badge">${fmtMoney(l.amount)}</span><button class="btn sm" data-lic="${esc(l.key)}">Manage</button></div></div>`;

export default async function (root, appId) {
  const a = await api('/apps/' + appId);
  const isAdmin = state.me.role === 'admin';
  const curl = `curl -X POST "${a.endpoint}" \\\n  -H "X-API-Key: ${isAdmin ? a.api_key : 'YOUR_API_KEY'}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"license_key":"ABCDEFGHJKLMNPQRS2","device_id":"unique-device-id"}'`;
  root.innerHTML = `<div class="page-head"><div class="row">${avatar(a.image_url, a.name, 'lg')}<div><h1>${esc(a.name)}</h1><p class="mono">${esc(a.id)}</p></div></div>
    <div class="row w-only"><button class="btn" id="editApp">✏️ Edit</button><button class="btn danger" id="delApp">🗑 Delete</button><button class="btn primary" id="newLic">＋ Create license</button></div></div>
    <div class="card"><div class="stack">
      <label class="field"><span>Endpoint (auto-generated)</span><div class="row"><code class="grow">${esc(a.endpoint)}</code><button class="btn sm" data-copy-text="${esc(a.endpoint)}">Copy</button></div></label>
      ${isAdmin ? `<label class="field"><span>API key — send it as the X-API-Key header</span><div class="row"><code class="grow" id="keyBox">••••••••••••••••••••</code>
        <button class="btn sm" id="revealKey">Reveal</button><button class="btn sm" data-copy-text="${esc(a.api_key)}">Copy</button><button class="btn sm danger" id="regenKey">Regenerate</button></div></label>` : ''}
    </div></div>
    <div id="blocks"></div>
    <div class="section-title"><h2>🔑 License keys</h2><label class="search"><span>🔎</span><input id="q" type="search" placeholder="Search name, @username, chat id, key" maxlength="64"></label></div>
    <div id="lics" class="list">${skeleton(3)}</div><div id="pg"></div>
    <div class="section-title"><h2>🔌 Connect your app</h2></div>
    <div class="card"><p class="muted">Your app sends the license key and a stable device id. The first device that verifies is locked to the key.</p>
    <div class="codebox">${esc(curl)}</div><p class="tiny muted">Full request/response reference: <b>API_DOCS.md</b> in your project.</p></div>`;

  rangeBlocks($('#blocks', root), {
    fetch: (s, e) => api('/stats', { params: { app_id: appId, start: s, end: e } }),
    build: (d) => [{ title: '👥 Active users', data: d.active, fmt: fmtNum }, { title: '💸 Earnings', data: d.earning, fmt: fmtMoney,
      sub: Object.fromEntries(Object.entries(d.sales).map(([k, v]) => [k, `${fmtNum(v)} sale${v === 1 ? '' : 's'}`])) }],
  });

  let page = 1, items = [];
  async function loadLics() {
    try {
      const d = await api(`/apps/${appId}/licenses`, { params: { q: $('#q', root).value.trim(), page } });
      items = d.items;
      $('#lics', root).innerHTML = d.items.length ? d.items.map(licRow).join('') : empty('🗝️', 'No license keys yet.');
      pager($('#pg', root), d, (p) => { page = p; loadLics(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  loadLics();
  $('#q', root).addEventListener('input', debounce(() => { page = 1; loadLics(); }));
  $('#lics', root).addEventListener('click', (e) => {
    const b = e.target.closest('[data-lic]');
    if (b) licenseModal(items.find((x) => x.key === b.dataset.lic), loadLics);
  });

  const reveal = $('#revealKey', root);
  if (reveal) {
    reveal.addEventListener('click', () => { const box = $('#keyBox', root); const show = box.textContent.startsWith('•'); box.textContent = show ? a.api_key : '••••••••••••••••••••'; reveal.textContent = show ? 'Hide' : 'Reveal'; });
    $('#regenKey', root).addEventListener('click', async () => {
      if (!await confirmBox('Regenerate API key?', 'Apps using the old key stop working until you update them.', 'Regenerate')) return;
      await api(`/apps/${appId}/regenerate-key`, { method: 'POST' }); toast('New API key generated'); location.reload();
    });
  }
  $('#newLic', root).addEventListener('click', () => openModal({
    title: 'Create license key', submit: 'Create key 🔑',
    body: `<label class="field"><span>Full name</span><input type="text" name="full_name" maxlength="80" required></label>
      <div class="two"><label class="field"><span>Telegram chat ID</span><input type="text" name="chat_id" inputmode="numeric" required placeholder="123456789"></label>
      <label class="field"><span>Username</span><input type="text" name="username" maxlength="33" placeholder="@username"></label></div>
      <label class="field"><span>Profile image URL</span><input type="url" name="profile_image_url" maxlength="500" placeholder="https://…"></label>
      <div class="two"><label class="field"><span>Amount (₹)</span><input type="number" name="amount" min="0" step="any" value="0" required></label>
      <label class="field"><span>Validity</span><div class="two"><input type="number" name="validity_value" min="1" value="30" required><select name="validity_unit"><option value="days">Days</option><option value="hours">Hours</option></select></div></label></div>`,
    onSubmit: async (v) => { const l = await api(`/apps/${appId}/licenses`, { method: 'POST', body: v }); toast('Key created: ' + l.key); copyText(l.key); page = 1; loadLics(); },
  }));
  $('#editApp', root).addEventListener('click', () => openModal({
    title: 'Edit app',
    body: `<label class="field"><span>App name</span><input type="text" name="name" maxlength="60" required value="${esc(a.name)}"></label>
      <label class="field"><span>Image URL</span><input type="url" name="image_url" maxlength="500" value="${esc(a.image_url)}"></label>`,
    onSubmit: async (v) => { await api('/apps/' + appId, { method: 'PATCH', body: v }); toast('Saved'); location.reload(); },
  }));
  $('#delApp', root).addEventListener('click', async () => {
    if (!await confirmBox('Delete this app?', 'It is hidden from the panel and its API stops answering. All data stays in the database.', 'Delete app')) return;
    await api('/apps/' + appId, { method: 'DELETE' }); toast('App deleted'); location.hash = '#/';
  });
}
