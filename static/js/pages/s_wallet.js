// Seller wallet: balance, withdraw (name, UPI id, amount → you will receive x after the fee), earning history + withdraw history.
import { $, api, empty, esc, fld, fmtDate, fmtMoney, fmtNum, lightbox, openModal, pager, skeleton, tabsBar, tileHtml, toast } from '../core.js';

export default async function (root) {
  let tab = 'earn', page = 1, w;
  root.innerHTML = `<div class="page-head"><div><h1>👛 Wallet</h1><p>Every sale is credited here automatically.</p></div><button class="btn primary" id="wd">💸 Withdraw</button></div><div id="tiles" class="grid cols-4 keep2"></div>
    <div id="tb" class="gap-top"></div><div id="list" class="list gap-top">${skeleton(3)}</div><div id="pg"></div>`;
  const tb = tabsBar([['earn', '💰 Earning history'], ['wd', '🏧 Withdraw history']], tab, (k) => { tab = k; page = 1; load(); });
  $('#tb', root).appendChild(tb.el);
  async function head() {
    w = await api('/wallet');
    $('#tiles', root).innerHTML = tileHtml('Balance', fmtMoney(w.balance), 'available', { big: true }) + tileHtml('Total earned', fmtMoney(w.total_earned), 'lifetime') + tileHtml('Pending withdrawals', fmtMoney(w.pending), 'waiting for approval') + tileHtml('Withdraw rules', `min ${fmtMoney(w.min_withdraw)}`, `${w.fee_pct}% fee`);
  }
  async function load() {
    try {
      if (tab === 'earn') {
        const d = await api('/wallet/history', { params: { page } });
        $('#list', root).innerHTML = d.items.length ? d.items.map((e) => `<div class="li wide"><div class="avatar">➕</div><div class="meta"><b>${esc(e.app_name)} · ${esc(e.plan_label)}</b><small>buyer ${esc(e.buyer || '—')} · ${fmtDate(e.ts)}</small></div><div class="acts"><b class="pos">+${fmtMoney(e.amount)}</b></div></div>`).join('') : empty('💰', 'No earnings yet.');
        pager($('#pg', root), d, (p) => { page = p; load(); });
      } else {
        const d = await api('/wallet/withdrawals', { params: { page } });
        $('#list', root).innerHTML = d.items.length ? d.items.map((x) => `<div class="li wide"><div class="avatar">➖</div><div class="meta"><b>${esc(x.name)} · <span class="mono">${esc(x.upi_id)}</span></b><small>${fmtDate(x.created_at)} · fee ${x.fee_pct}% (${fmtMoney(x.fee)}) · you receive ${fmtMoney(x.receive)}</small>${x.reason ? `<small>${esc(x.reason)}</small>` : ''}</div>
          <div class="acts"><b class="neg">−${fmtMoney(x.amount)}</b><span class="badge ${{ pending: 'warn', approved: 'ok', rejected: 'bad' }[x.status]}">${x.status}</span>${x.proof_url ? `<img class="shot" src="${esc(x.proof_url)}" data-shot="${esc(x.proof_url)}" alt="proof">` : ''}</div></div>`).join('') : empty('🏧', 'No withdrawals yet.');
        pager($('#pg', root), d, (p) => { page = p; load(); });
      }
    } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('click', (e) => { const s = e.target.closest('[data-shot]'); if (s) lightbox(s.dataset.shot); });
  $('#wd', root).onclick = () => {
    const m = openModal({ title: '💸 Withdraw', submit: 'Request withdrawal', body: `<p class="muted tiny">Balance <b>${fmtMoney(w.balance)}</b> · minimum <b>${fmtMoney(w.min_withdraw)}</b> · fee <b>${w.fee_pct}%</b></p>${fld('Your name', '<input name="name" required maxlength="60">')}${fld('UPI ID', '<input name="upi_id" required maxlength="80" placeholder="name@bank">')}${fld('Amount ₹ (whole number)', `<input name="amount" type="number" min="${w.min_withdraw}" max="${Math.floor(w.balance)}" step="1" required>`)}<div class="seller-note" id="q">Enter an amount to see what you will receive.</div>`,
      onSubmit: async (v) => { await api('/wallet/withdraw', { method: 'POST', body: v }); toast('Request sent — you will be notified 🎉'); head(); tab = 'wd'; tb.set('wd'); load(); } });
    const amt = m.form.elements.amount; let t;
    amt.addEventListener('input', () => { clearTimeout(t); t = setTimeout(async () => { if (!amt.value) return; try { const q = await api('/wallet/quote', { params: { amount: amt.value } }); $('#q', m.el).innerHTML = `Fee ${q.fee_pct}% = ${fmtMoney(q.fee)} → <b>you will receive ${fmtMoney(q.receive)}</b>`; } catch { /* ignore */ } }, 250); });
  };
  await head(); load();
}
