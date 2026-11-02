import { $, api, copyText, empty, esc, fmtMoney, fmtNum, openModal, rangeBlocks, state, toast, avatar } from './core.js';

function appCard(a) {
  return `<div class="card app-card" data-app="${esc(a.id)}" tabindex="0" role="link">
    <div class="top">${avatar(a.image_url, a.name, 'lg')}<div class="grow"><h3>${esc(a.name)}</h3><small class="muted mono">${esc(a.id)}</small></div></div>
    <div class="stats"><div><small>Active today</small><b>${fmtNum(a.today_active)}</b></div><div><small>Earned today</small><b>${fmtMoney(a.today_earning)}</b></div>
    <div><small>Licenses</small><b>${fmtNum(a.licenses)}</b></div><div><small>Endpoint</small><b class="tiny">/api/v1/…/verify</b></div></div></div>`;
}

function newAppModal(onDone) {
  openModal({
    title: 'Add a new app', submit: 'Create app 🚀',
    body: `<label class="field"><span>App name</span><input type="text" name="name" maxlength="60" required placeholder="e.g. Super Tool"></label>
      <label class="field"><span>Image URL</span><input type="url" name="image_url" maxlength="500" placeholder="https://…/icon.png"></label>
      <p class="tiny muted">Your API endpoint and API key are generated automatically.</p>`,
    onSubmit: async (v, form, close) => {
      const a = await api('/apps', { method: 'POST', body: v });
      close();
      openModal({
        title: 'App created 🎉', noFoot: true,
        body: `<p class="muted">Use these in your app. The key stays visible on the app page for admins.</p>
          <label class="field"><span>Endpoint (auto-generated)</span><div class="row"><code class="grow">${esc(a.endpoint)}</code><button type="button" class="btn sm" data-copy-text="${esc(a.endpoint)}">Copy</button></div></label>
          <label class="field"><span>API key</span><div class="row"><code class="grow">${esc(a.api_key)}</code><button type="button" class="btn sm" data-copy-text="${esc(a.api_key)}">Copy</button></div></label>
          <button type="button" class="btn primary block" data-x>Done</button>` });
      onDone();
      return false;
    },
  });
}

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>Hey ${esc(state.me.username)} 👋</h1><p>Here’s how your apps are doing.</p></div>
    <button class="btn primary w-only" id="addApp">＋ New app</button></div>
    <div class="grid cols-3"><div class="tile big-total"><div class="lbl">Total apps</div><div class="val" id="totalApps">…</div><div class="sub">live on your panel</div></div></div>
    <div id="blocks"></div>
    <div class="section-title"><h2>📱 My apps</h2></div><div id="apps" class="grid auto"></div>`;
  const draw = async () => rangeBlocks($('#blocks', root), {
    fetch: (s, e) => api('/dashboard', { params: { start: s, end: e } }),
    build: (d) => [
      { title: '👥 Active users', data: d.summary.active, fmt: fmtNum },
      { title: '💸 Earnings', data: d.summary.earning, fmt: fmtMoney,
        sub: Object.fromEntries(Object.entries(d.summary.sales).map(([k, v]) => [k, `${fmtNum(v)} sale${v === 1 ? '' : 's'}`])) },
    ],
    after: (d) => {
      $('#totalApps', root).textContent = fmtNum(d.total_apps);
      $('#apps', root).innerHTML = d.apps.length ? d.apps.map(appCard).join('') : `<div class="card">${empty('🌱', 'No apps yet — add your first one!')}</div>`;
    },
  });
  await draw();
  $('#addApp', root).addEventListener('click', () => newAppModal(() => draw()));
  $('#apps', root).addEventListener('click', (e) => { const c = e.target.closest('[data-app]'); if (c) location.hash = '#/apps/' + c.dataset.app; });
  $('#apps', root).addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.click(); });
}
