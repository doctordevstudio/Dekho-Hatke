// Admin home: Active users + Earnings + Purchases (today / yesterday / all time / custom) and one card per app.
// Every tile opens a paginated list of what is behind the number.
import { $, api, avatar, drill, empty, esc, fmtDate, fmtMoney, fmtNum, pager, rangeBlocks, rangeLabel, skeleton, statusBadge, tileHtml, toast, until } from '../core.js';
import { userDetail } from './userui.js';

export function drillActive(range, rng, appId = '') {
  const m = drill({ title: `👥 Active users · ${rangeLabel(range)}`, header: (d) => `<p class="muted">${fmtNum(d.count)} distinct license${d.count === 1 ? '' : 's'} used the app API.</p>`,
    load: (p) => api('/stats/active', { params: { range, ...rng, app_id: appId, page: p } }),
    row: (r) => `<div class="li wide" data-chat="${esc(r.chat_id)}" tabindex="0"><div class="avatar">🔑</div><div class="meta"><b>${esc(r.full_name || '—')}</b>
      <small>${r.username ? '@' + esc(r.username) + ' · ' : ''}<span class="mono">${esc(r.chat_id)}</span> · ${esc(r.app_name)}</small><small class="muted">last seen ${fmtDate(r.last_seen)}</small></div><div class="acts">${statusBadge(r.status)}</div></div>` });
  m.el.addEventListener('click', (e) => { const c = e.target.closest('[data-chat]'); if (c && c.dataset.chat !== 'test') userDetail(c.dataset.chat); });
}

export function drillSales(range, rng, appId = '', title = '') {
  const m = drill({ title: title || `💸 Sales · ${rangeLabel(range)}`, header: (d) => `<div class="kpi-row">${tileHtml('Earned', fmtMoney(d.amount_total), `${fmtNum(d.sale_count)} sale${d.sale_count === 1 ? '' : 's'}`, { big: true })}</div>`,
    load: (p) => api('/earnings', { params: { range, ...rng, app_id: appId, page: p } }),
    row: (e) => `<div class="li wide" data-chat="${esc(e.user.chat_id)}" tabindex="0">${avatar(e.user.profile_image_url, e.user.full_name, 'lg')}<div class="meta"><b>${esc(e.user.full_name || '—')}</b>
      <small>${e.user.username ? '@' + esc(e.user.username) + ' · ' : ''}<span class="mono">${esc(e.user.chat_id)}</span></small><div class="chips"><span class="badge violet">📱 ${esc(e.app_name || 'Deposit')}</span><span class="badge">${esc(e.label)}</span></div></div>
      <div class="acts"><b>${fmtMoney(e.amount)}</b><small class="muted">${fmtDate(e.ts)}</small></div></div>` });
  m.el.addEventListener('click', (e) => { const c = e.target.closest('[data-chat]'); if (c) userDetail(c.dataset.chat); });
}

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>🏠 Home</h1><p>Your shop at a glance. Tap any tile to see everything behind the number.</p></div></div>
    <div id="top" class="grid cols-4 keep2">${skeleton(1)}</div>
    <div id="blocks"></div>
    <div class="section-title"><h2>📱 Apps</h2><span class="tiny muted" id="cnt"></span></div>
    <div id="apps" class="grid auto"></div><div id="pg"></div>`;
  let page = 1;
  async function apps() {
    const d = await api('/dashboard', { params: { page } });
    $('#cnt', root).textContent = `${d.total_apps} app${d.total_apps === 1 ? '' : 's'}`;
    $('#apps', root).innerHTML = d.apps.length ? d.apps.map((a) => `<div class="card app-card" data-app="${esc(a.id)}" tabindex="0"><div class="top">${avatar(a.image_url, a.name, 'lg')}
      <div class="grow"><b>${esc(a.name)}</b><br><small class="muted">${a.seller_name ? '🚀 ' + esc(a.seller_name) : 'by you'}</small></div>
      <span class="badge ${a.disabled ? 'bad' : a.status === 'live' ? 'ok' : 'warn'}">${a.disabled ? 'disabled' : a.status.replace('_', ' ')}</span></div>
      <div class="stats"><div><small>Active today</small><b>${fmtNum(a.today_active)}</b></div><div><small>Earned today</small><b>${fmtMoney(a.today_earning)}</b></div>
      <div><small>Licenses</small><b>${fmtNum(a.licenses)}</b></div><div><small>Purchases</small><b>${fmtNum(a.sales_count)}</b></div></div></div>`).join('') : empty('📱', 'No apps yet — add one in Purchase.');
    pager($('#pg', root), d, (p) => { page = p; apps(); });
    return d;
  }
  const first = await apps();
  const tiles = (d) => { $('#top', root).innerHTML = tileHtml('Total users', fmtNum(d.total_users), 'tap to open', { big: true, drill: 'users' }) + tileHtml('Apps', fmtNum(d.total_apps), 'in the shop', { drill: 'purchase' }) +
    tileHtml('Awaiting review', fmtNum(d.pending_review), 'seller apps', { hot: d.pending_review > 0, drill: 'review' }) + tileHtml('Today earned', fmtMoney(d.summary.earning.today), `${fmtNum(d.summary.sales.today)} purchases`, { drill: 'today' }); };
  tiles(first);
  root.addEventListener('click', (e) => { const c = e.target.closest('[data-app]'); if (c) location.hash = '#/apps/' + c.dataset.app; });
  $('#top', root).addEventListener('click', (e) => { const t = e.target.closest('[data-drill]'); if (!t) return; const k = t.dataset.drill;
    if (k === 'users') location.hash = '#/users'; else if (k === 'purchase') location.hash = '#/purchase'; else if (k === 'review') location.hash = '#/seller'; else if (k === 'today') drillSales('today', {}); });
  await rangeBlocks($('#blocks', root), {
    fetch: (start, end) => api('/dashboard', { params: { start, end, page } }),
    build: (d) => [
      { title: '👥 Active users', key: 'active', data: d.summary.active, fmt: fmtNum },
      { title: '💸 Earnings', key: 'earning', data: d.summary.earning, fmt: fmtMoney, sub: Object.fromEntries(['today', 'yesterday', 'all', 'custom'].map((k) => [k, d.summary.sales[k] == null ? '' : `${fmtNum(d.summary.sales[k])} purchases`])) },
      { title: '🛒 Total purchases', key: 'sales', data: d.summary.sales, fmt: fmtNum }],
    onDrill: (key, range, rng) => (key === 'active' ? drillActive(range, rng) : drillSales(range, rng)),
  });
}
