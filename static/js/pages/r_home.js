// Reseller home: counts (price increases only apply to this bot) + earnings / commission by range with drill-down.
import { $, api, bindDrill, drill, empty, esc, fmtDate, fmtMoney, fmtNum, rangeBlocks, rangeLabel, skeleton, state, tileHtml, avatar } from '../core.js';
import { userDetail } from './userui.js';

export default async function (root) {
  const d0 = await api('/dashboard');
  const me = state.me;
  root.innerHTML = `<div class="page-head"><div><h1>🏠 Home</h1><p>Welcome, ${esc(me.name)} 👋 — your store at a glance.</p></div></div>
    ${d0.forced ? `<div class="card forced"><b>⛔ Your store is paused.</b> Your earnings reached the limit. Clear the dues with the admin to reopen it. <a href="#/due">See dues →</a></div>` : ''}
    <div class="grid cols-4 keep2"><div class="tile big-total"><div class="lbl">Categories</div><div class="val">${fmtNum(d0.counts.categories)}</div></div><div class="tile"><div class="lbl">Apps</div><div class="val">${fmtNum(d0.counts.apps)}</div></div>
      <div class="tile"><div class="lbl">Plans</div><div class="val">${fmtNum(d0.counts.plans)}</div></div><div class="tile click" data-go="users" tabindex="0"><div class="lbl">Your users</div><div class="val">${fmtNum(d0.users)}</div><div class="sub"><span class="more">view all ›</span></div></div></div>
    <div id="blocks"></div>`;
  $('[data-go]', root).onclick = () => (location.hash = '#/users');
  const sales = (range, rng) => { const m = drill({ title: `💸 Sales · ${rangeLabel(range)}`, header: (d) => `<div class="kpi-row">${tileHtml('Earned', fmtMoney(d.amount_total), `${fmtNum(d.sale_count)} sales`, { big: true })}${tileHtml('Admin commission', fmtMoney(d.commission_total), 'due to the admin', { hot: true })}</div>`,
    load: (p) => api('/earnings', { params: { range, ...rng, page: p } }),
    row: (e) => `<div class="li wide" data-chat="${esc(e.user.chat_id)}">${avatar(e.user.profile_image_url, e.user.full_name, 'lg')}<div class="meta"><b>${esc(e.user.full_name || '—')}</b><small>${esc(e.app_name || 'Deposit')} · ${esc(e.label)} · ${fmtDate(e.ts)}</small></div><div class="acts"><b>${fmtMoney(e.amount)}</b>${e.commission ? `<span class="badge warn">commission ${fmtMoney(e.commission)}</span>` : ''}</div></div>` });
    m.el.addEventListener('click', (e) => { const c = e.target.closest('[data-chat]'); if (c) userDetail(c.dataset.chat); }); };
  await rangeBlocks($('#blocks', root), {
    fetch: (start, end) => api('/dashboard', { params: { start, end } }),
    build: (d) => [{ title: '💸 Your earnings', key: 'earn', data: Object.fromEntries(Object.entries(d.ranges).map(([k, v]) => [k, v.earned])), fmt: fmtMoney, sub: Object.fromEntries(Object.entries(d.ranges).map(([k, v]) => [k, `${fmtNum(v.sales)} sales`])) },
      { title: '🏦 Admin commission', key: 'comm', data: Object.fromEntries(Object.entries(d.ranges).map(([k, v]) => [k, v.commission])), fmt: fmtMoney }],
    onDrill: (_k, range, rng) => sales(range, rng),
  });
}
