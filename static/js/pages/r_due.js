// Reseller dues: earnings since the last settlement, commission due, maintenance limit.
import { $, api, empty, esc, fmtDate, fmtMoney, fmtNum, pager, tileHtml, toast } from '../core.js';

export default async function (root) {
  let page = 1;
  root.innerHTML = '<div class="page-head"><div><h1>🧾 Due</h1><p>When your earnings reach the limit, your bot goes into maintenance until you settle the commission with the admin.</p></div></div><div id="body"></div>';
  async function load() {
    try {
      const d = await api('/due', { params: { page } }), u = d.due;
      const pct = u.limit ? Math.min(100, Math.round((u.earned / u.limit) * 100)) : 0;
      $('#body', root).innerHTML = `<div class="grid cols-4 keep2">${tileHtml('Earned since settlement', fmtMoney(u.earned), `${fmtNum(u.sales)} sales`, { big: true })}${tileHtml('Commission due', fmtMoney(u.commission), `${u.commission_pct}% of sales`, { hot: true })}
        ${tileHtml('Maintenance limit', u.limit ? fmtMoney(u.limit) : 'none', u.limit ? `${pct}% used` : '')}${tileHtml('Since', fmtDate(u.period_start), 'last settlement')}</div>
        ${u.limit ? `<div class="bar gap-top"><i style="width:${pct}%"></i></div>` : ''}${u.forced ? '<div class="card forced gap-top"><b>⛔ Your store is paused.</b> Pay the commission to the admin to reopen it.</div>' : ''}
        ${d.message ? `<div class="seller-note gap-top">${esc(d.message)}</div>` : ''}
        <div class="section-title"><h2>Sales in this period</h2></div><div class="list">${d.items.length ? d.items.map((e) => `<div class="li wide"><div class="avatar">💵</div><div class="meta"><b>${esc(e.user)}</b><small>${esc(e.app_name)} · ${fmtDate(e.ts)}</small></div><div class="acts"><b>${fmtMoney(e.amount)}</b><span class="badge warn">commission ${fmtMoney(e.commission)}</span></div></div>`).join('') : empty('🪙', 'No sales yet in this period.')}</div><div id="pg"></div>
        ${d.settlements.length ? `<div class="section-title"><h2>Past settlements</h2></div><div class="list">${d.settlements.map((s) => `<div class="li"><div class="avatar">✅</div><div class="meta"><b>${fmtMoney(s.commission)} settled</b><small>${fmtMoney(s.earned)} earned · ${fmtNum(s.sales)} sales · ${fmtDate(s.ts)}</small></div></div>`).join('')}</div>` : ''}`;
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  load();
}
