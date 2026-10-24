// Seller dashboard: earnings + sales by range (drill-down to every sale), apps by status, wallet balance.
import { $, api, drill, empty, esc, fmtDate, fmtMoney, fmtNum, rangeBlocks, rangeLabel, state, tileHtml, avatar, bindDrill } from '../core.js';

export function sellerSales(range, rng, appId = '', title = '') {
  drill({ title: title || `💰 Sales · ${rangeLabel(range)}`, header: (d) => `<div class="kpi-row">${tileHtml('You earned', fmtMoney(d.amount_total), `${fmtNum(d.count)} sale${d.count === 1 ? '' : 's'}`, { big: true })}</div>`,
    load: (p) => api('/earnings', { params: { range, ...rng, app_id: appId, page: p } }),
    row: (e) => `<div class="li wide"><div class="avatar">💵</div><div class="meta"><b>${esc(e.buyer || '—')}</b><small>bought ${esc(e.app_name)} · ${esc(e.plan_label)} · ${fmtDate(e.ts)}</small></div><div class="acts"><b>${fmtMoney(e.amount)}</b></div></div>` });
}

export default async function (root) {
  const d0 = await api('/dashboard');
  root.innerHTML = `<div class="page-head"><div><h1>📊 Dashboard</h1><p>Welcome, ${esc(state.me.name)} 👋 Tap any tile to see what is behind it.</p></div></div>
    ${state.me.telegram_linked ? '' : '<div class="seller-note">📲 Connect your Telegram (Settings) to get sale, approval and withdrawal alerts.</div>'}
    <div class="grid cols-4 keep2 gap-top" id="top"></div><div id="blocks"></div>`;
  const status = (k, t) => { const m = drill({ title: t, load: (p) => api('/apps-summary', { params: { status: k, page: p } }),
    row: (a) => `<div class="li wide" data-go="#/apps/${esc(a.id)}" tabindex="0">${avatar(a.image_url, a.name, 'lg')}<div class="meta"><b>${esc(a.name)}</b><small>${fmtNum(a.sales)} sales · ${fmtMoney(a.earned)}</small></div><div class="acts"><span class="badge ${a.status === 'live' ? 'ok' : a.status === 'rejected' ? 'bad' : 'warn'}">${esc(a.status_label)}</span></div></div>` });
    m.el.addEventListener('click', (e) => { const g = e.target.closest('[data-go]'); if (g) { m.close(); location.hash = g.dataset.go; } }); };
  $('#top', root).innerHTML = tileHtml('Wallet balance', fmtMoney(d0.balance), d0.pending_withdraw ? `${fmtMoney(d0.pending_withdraw)} withdrawing` : 'tap to withdraw', { big: true, drill: 'wallet' }) +
    tileHtml('Total sells', fmtNum(d0.sales_count), `earned ${fmtMoney(d0.total_earned)}`, { drill: 'all' }) + tileHtml('Apps live', fmtNum(d0.apps.live), `${d0.apps_total} total`, { drill: 'live' }) +
    tileHtml('In verification', fmtNum(d0.apps.in_verification), `${d0.apps.rejected} rejected`, { hot: d0.apps.in_verification > 0, drill: 'in_verification' });
  bindDrill($('#top', root), (k) => { if (k === 'wallet') location.hash = '#/wallet'; else if (k === 'all') sellerSales('all', {}); else status(k, k === 'live' ? '✅ Live apps' : '🕒 Apps in verification'); });
  await rangeBlocks($('#blocks', root), {
    fetch: (start, end) => api('/dashboard', { params: { start, end } }),
    build: (d) => [{ title: '💰 Earnings', key: 'earn', data: Object.fromEntries(Object.entries(d.ranges).map(([k, v]) => [k, v.earned])), fmt: fmtMoney, sub: Object.fromEntries(Object.entries(d.ranges).map(([k, v]) => [k, `${fmtNum(v.sales)} sales`])) },
      { title: '🛒 Sales', key: 'sales', data: Object.fromEntries(Object.entries(d.ranges).map(([k, v]) => [k, v.sales])), fmt: fmtNum }],
    onDrill: (_k, range, rng) => sellerSales(range, rng),
  });
}
