import { $, api, avatar, empty, esc, fmtDate, fmtMoney, fmtNum, pager, plural, rangeBar, skeleton, state, tileHtml, toast } from '../core.js';
import { userDetail } from './userui.js';

export default async function (root) {
  const reseller = state.panel === 'reseller';
  let rng = { range: 'today', start: '', end: '' }, appId = '', page = 1;
  root.innerHTML = `<div class="page-head"><div><h1>💸 Earning</h1><p>Every sale, with the buyer’s details${reseller ? ' and the admin commission' : ''}. Tap a tile to filter.</p></div><div id="rb"></div></div>
    <div class="grid cols-3" id="sum">${skeleton(1)}</div>
    <div class="section-title"><h2>Sales</h2></div><div id="list" class="list"></div><div id="pg"></div>`;
  const bar = rangeBar((r) => { rng = r; page = 1; appId = ''; load(); });
  $('#rb', root).appendChild(bar.el);
  async function load() {
    try {
      const d = await api('/earnings', { params: { ...rng, app_id: appId, page } });
      $('#sum', root).innerHTML = tileHtml('Total earned', fmtMoney(d.amount_total), plural(d.sale_count, 'sale'), { big: true, drill: 'all' }) +
        (reseller ? tileHtml('Admin commission', fmtMoney(d.commission_total), 'due to the admin') : '') +
        d.by_app.map((a) => tileHtml(esc(a.app_name || 'Deposit'), fmtMoney(a.amount), plural(a.count, 'sale'), { hot: true, drill: 'app:' + (a.app_id || '') })).join('');
      $('#list', root).innerHTML = d.items.length ? d.items.map((e, i) => `<div class="li" data-i="${i}" data-chat="${esc(e.user.chat_id)}" tabindex="0">${avatar(e.user.profile_image_url, e.user.full_name, 'lg')}<div class="meta"><b>${esc(e.user.full_name || '—')}</b><small>${e.user.username ? '@' + esc(e.user.username) : 'no username'} · <span class="mono">${esc(e.user.chat_id)}</span></small>
        <div class="chips"><span class="badge violet">📱 ${esc(e.app_name)}</span><span class="badge">${esc(e.label)}</span>${e.license_key ? `<span class="chip mono" data-copy-text="${esc(e.license_key)}">🔑 ${esc(e.license_key)}</span>` : ''}</div></div>
        <div class="acts"><b>${fmtMoney(e.amount)}</b>${e.commission ? `<span class="badge warn">commission ${fmtMoney(e.commission)}</span>` : ''}<span class="badge ${e.source === 'manual' ? 'warn' : 'ok'}">${e.source === 'manual' ? '✍️ manual' : e.source === 'wallet' ? '👛 wallet' : '🏦 UPI'}</span><small class="muted">${fmtDate(e.ts)}</small></div></div>`).join('')
        : empty('🪙', 'No earnings in this period.');
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('click', (e) => {
    const t = e.target.closest('[data-drill]');
    if (t) { const k = t.dataset.drill; appId = k.startsWith('app:') ? k.slice(4) : ''; page = 1; load(); return; }
    const r = e.target.closest('[data-chat]');
    if (r && !e.target.closest('[data-copy-text]')) userDetail(r.dataset.chat);
  });
  load();
}
