import { $, api, avatar, empty, esc, fmtMoney, skeleton, tabsBar, toast } from '../core.js';
import { userDetail } from './userui.js';

export default async function (root) {
  let tab = 'referral';
  root.innerHTML = `<div class="page-head"><div><h1>🏆 Leaderboard</h1><p>Top 50 by balance. Referral = refer money earned · Reseller = total sales through their store.</p></div></div>
    <div id="tb"></div><div id="list" class="list gap-top">${skeleton(5)}</div>`;
  $('#tb', root).appendChild(tabsBar([['referral', '🎁 Referral'], ['reseller', '🏪 Reseller']], tab, (k) => { tab = k; load(); }).el);
  async function load() {
    $('#list', root).innerHTML = skeleton(5);
    try {
      const d = await api('/leaderboard', { params: { tab } });
      const medal = (i) => ['🥇', '🥈', '🥉'][i] || `#${i + 1}`;
      $('#list', root).innerHTML = d.items.length ? d.items.map((r, i) => `<div class="li" ${tab === 'referral' ? `data-chat="${esc(r.chat_id)}" tabindex="0"` : ''}>
        <div class="big-num" style="min-width:56px;font-size:22px;text-align:center">${medal(i)}</div>${avatar(r.profile_image_url, r.full_name, 'lg')}
        <div class="meta"><b>${esc(r.full_name || '—')}</b><small>${r.username ? '@' + esc(r.username) + ' · ' : ''}<span class="mono">${esc(r.chat_id)}</span></small></div>
        <div class="acts"><b>${fmtMoney(r.balance)}</b>${tab === 'reseller' ? `<small class="muted">commission due ${fmtMoney(r.due)}</small>` : ''}</div></div>`).join('') : empty('🏆', 'Nobody on the board yet.');
    } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('click', (e) => { const r = e.target.closest('[data-chat]'); if (r) userDetail(r.dataset.chat); });
  load();
}
