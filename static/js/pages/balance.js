import { $, api, avatar, debounce, empty, esc, fld, fmtDate, fmtMoney, pager, skeleton, state, tabsBar, tileHtml, toast } from '../core.js';
import { userDetail } from './userui.js';

export default async function (root) {
  const admin = state.panel === 'admin';
  let tab = 'wallets', page = 1, q = '';
  root.innerHTML = `<div class="page-head"><div><h1>👛 Balance</h1><p>Every user’s wallet (Deposit money and Refer money) and the full wallet ledger.</p></div></div>
    <div id="tiles" class="grid cols-3"></div>
    ${admin ? '<div id="cfg"></div>' : ''}
    <div class="row between gap-top"><div id="tb"></div><label class="search grow"><span>🔎</span><input id="q" type="search" placeholder="Search name, @username, chat id" maxlength="64"></label></div>
    <div id="list" class="list">${skeleton(4)}</div><div id="pg"></div>`;
  const tb = tabsBar([['wallets', '👛 Wallets'], ['ledger', '📒 Ledger']], tab, (k) => { tab = k; page = 1; load(); });
  $('#tb', root).appendChild(tb.el);
  async function load() {
    try {
      if (tab === 'wallets') {
        const d = await api('/balances', { params: { q, page } });
        $('#tiles', root).innerHTML = tileHtml('Deposit money', fmtMoney(d.totals.deposit), 'held by users', { big: true }) + tileHtml('Refer money', fmtMoney(d.totals.refer), 'earned by referrers', { hot: true }) +
          tileHtml('Min deposit / withdraw', `${fmtMoney(d.settings.min_deposit)} / ${fmtMoney(d.settings.min_withdraw)}`, admin ? 'edit below' : 'set by the admin');
        if (admin && !$('#cfg', root).innerHTML) drawCfg(d.settings);
        $('#list', root).innerHTML = d.items.length ? d.items.map((u) => `<div class="li" data-chat="${esc(u.chat_id)}" tabindex="0">${avatar(u.profile_image_url, u.full_name, 'lg')}
          <div class="meta"><b>${esc(u.full_name || '—')}</b><small class="mono">${esc(u.chat_id)}</small></div>
          <div class="acts"><span class="badge info">Deposit ${fmtMoney(u.deposit)}</span><span class="badge violet">Refer ${fmtMoney(u.refer)}</span>${u.bonus ? `<span class="badge ok">Bonus ${fmtMoney(u.bonus)}</span>` : ''}<b>${fmtMoney(u.total)}</b></div></div>`).join('') : empty('👛', 'No wallets yet.');
        pager($('#pg', root), d, (p) => { page = p; load(); });
      } else {
        const d = await api('/wallet/ledger', { params: { page } });
        $('#list', root).innerHTML = d.items.length ? d.items.map((r) => `<div class="li" data-chat="${esc(r.chat_id)}" tabindex="0"><div class="avatar">${r.amount >= 0 ? '➕' : '➖'}</div>
          <div class="meta"><b>${esc(r.kind_label)}</b><small><span class="mono">${esc(r.chat_id)}</span> · ${esc(r.bucket)} · ${esc(r.note || '')} · ${fmtDate(r.ts)}</small></div>
          <div class="acts"><b class="${r.amount >= 0 ? 'pos' : 'neg'}">${r.amount >= 0 ? '+' : ''}${fmtMoney(r.amount)}</b><small class="muted">bal ${fmtMoney(r.balance_after)}</small></div></div>`).join('') : empty('📒', 'No wallet activity yet.');
        pager($('#pg', root), d, (p) => { page = p; load(); });
      }
    } catch (e) { toast(e.message, 'err'); }
  }
  function drawCfg(s) {
    $('#cfg', root).innerHTML = `<div class="card gap-top"><h3>⚙️ Wallet limits</h3><div class="two gap-top">${fld('Minimum deposit (₹)', `<input type="number" id="mind" min="1" value="${s.min_deposit}">`)}${fld('Minimum withdraw (₹)', `<input type="number" id="minw" min="1" value="${s.min_withdraw}">`)}</div>
      <button class="btn primary w-only" id="saveCfg">Save limits</button></div>`;
    $('#saveCfg', root).addEventListener('click', async () => {
      try { await api('/wallet/settings', { method: 'PUT', body: { min_deposit: $('#mind', root).value, min_withdraw: $('#minw', root).value } }); toast('Saved ✨'); load(); } catch (e) { toast(e.message, 'err'); }
    });
  }
  $('#q', root).addEventListener('input', debounce((e) => { q = e.target.value.trim(); page = 1; if (tab === 'wallets') load(); }));
  root.addEventListener('click', (e) => { const r = e.target.closest('[data-chat]'); if (r) userDetail(r.dataset.chat, load); });
  load();
}
