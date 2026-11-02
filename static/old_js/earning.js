import { $, $$, api, avatar, empty, esc, fmtDate, fmtMoney, fmtNum, pager, skeleton, toast } from './core.js';

export default async function (root) {
  let rng = 'today', start = '', end = '', appId = '', page = 1;
  root.innerHTML = `<div class="page-head"><div><h1>💸 Earning</h1><p>Revenue by app, with every buyer’s details.</p></div>
    <div class="seg" id="seg"><button data-r="today" class="on">Today</button><button data-r="yesterday">Yesterday</button><button data-r="all">All time</button><button data-r="custom">Custom</button></div></div>
    <div class="row" id="customRow" hidden><input type="date" id="from" aria-label="From"><input type="date" id="to" aria-label="To"><button class="btn primary sm" id="apply">Apply</button></div>
    <div class="grid cols-3" id="sum">${skeleton(1)}</div>
    <div class="section-title"><h2>Buyers</h2><div class="tabs" id="tabs"></div></div>
    <div id="list" class="list"></div><div id="pg"></div>`;

  async function load() {
    try {
      const d = await api('/earnings', { params: { range: rng, start, end, app_id: appId, page } });
      $('#sum', root).innerHTML = `<div class="tile big-total"><div class="lbl">Total earned</div><div class="val">${fmtMoney(d.amount_total)}</div><div class="sub">${fmtNum(d.sale_count)} sale${d.sale_count === 1 ? '' : 's'}</div></div>` +
        d.by_app.map((a) => `<div class="tile hot"><div class="lbl">${esc(a.app_name)}</div><div class="val">${fmtMoney(a.amount)}</div><div class="sub">${fmtNum(a.count)} sale${a.count === 1 ? '' : 's'}</div></div>`).join('');
      $('#tabs', root).innerHTML = `<button data-app="" class="${appId ? '' : 'on'}">All apps</button>` +
        d.by_app.map((a) => `<button data-app="${esc(a.app_id)}" class="${appId === a.app_id ? 'on' : ''}">${esc(a.app_name)}</button>`).join('');
      $('#list', root).innerHTML = d.items.length ? d.items.map((e) => `<div class="li">${avatar(e.user.profile_image_url, e.user.full_name, 'lg')}<div class="meta"><b>${esc(e.user.full_name || '—')}</b><small>${e.user.username ? '@' + esc(e.user.username) : 'no username'} · <span class="mono">${esc(e.user.chat_id)}</span></small>
        <div class="chips"><span class="badge violet">📱 ${esc(e.app_name)}</span><span class="badge">${esc(e.label)}</span><span class="chip mono" data-copy-text="${esc(e.license_key)}">🔑 ${esc(e.license_key)}</span></div></div>
        <div class="acts"><b>${fmtMoney(e.amount)}</b><span class="badge ${e.source === 'manual' ? 'warn' : 'ok'}">${e.source === 'manual' ? '✍️ manual' : '🏦 UPI'}</span><small class="muted">${fmtDate(e.ts)}</small></div></div>`).join('')
        : empty('🪙', 'No earnings in this period.');
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  $('#seg', root).addEventListener('click', (e) => {
    const b = e.target.closest('[data-r]'); if (!b) return;
    $$('#seg button', root).forEach((x) => x.classList.toggle('on', x === b));
    rng = b.dataset.r; page = 1; appId = '';
    $('#customRow', root).hidden = rng !== 'custom';
    if (rng !== 'custom') load();
  });
  $('#apply', root).addEventListener('click', () => {
    start = $('#from', root).value; end = $('#to', root).value;
    if (!start || !end) return toast('Pick both dates', 'err');
    page = 1; load();
  });
  $('#tabs', root).addEventListener('click', (e) => { const b = e.target.closest('[data-app]'); if (b) { appId = b.dataset.app; page = 1; load(); } });
  load();
}
