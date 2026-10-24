// UPI ID page (admin + reseller): UPI cards with earnings, transaction tabs (Pending / In verification / Failed / Success),
// search by Txn ID / UTR, 10-minute countdowns, verify / reject, per-UPI paginated payments.
import { $, $$, api, avatar, bindDrill, confirmBox, drill, empty, esc, fld, fmtDate, fmtMoney, lightbox, mmss, openModal, pager, rangeBar,
  rangeLabel, skeleton, state, tabsBar, tick, tileHtml, toast, debounce, copyText } from '../core.js';
import { userDetail } from './userui.js';

export default async function (root) {
  const reseller = state.panel === 'reseller';
  let status = 'pending', upi = 'all', page = 1, q = '', rng = { range: 'all', start: '', end: '' }, upis = [], rows = [];
  root.innerHTML = `<div class="page-head"><div><h1>🏦 UPI ID</h1><p>Payment IDs are used in round-robin. Each payment must be verified by the UPI owner or an admin within 10 minutes.</p></div>
      <button class="btn primary w-only" id="addUpi">＋ Add UPI ID</button></div>
    <div id="cards" class="grid auto">${skeleton(2)}</div>
    <div class="section-title"><h2>Transactions</h2></div>
    <div class="row between"><div id="tabs"></div><label class="search grow"><span>🔎</span><input id="q" type="search" placeholder="Search Txn ID or UTR number" maxlength="40"></label></div>
    <div class="row gap-top" id="filters"></div>
    <div id="list" class="list gap-top"></div><div id="pg"></div>`;

  const tabs = tabsBar([['pending', '⏳ Pending'], ['verification', '🕒 In verification'], ['failed', '❌ Failed'], ['success', '✅ Success']], status, (k) => { status = k; page = 1; loadTxns(); });
  $('#tabs', root).appendChild(tabs.el);
  const rb = rangeBar((r) => { rng = r; page = 1; loadTxns(); }, { initial: 'all' });
  $('#filters', root).appendChild(rb.el);
  const sel = document.createElement('select');
  sel.innerHTML = '<option value="all">All UPI IDs</option>';
  sel.addEventListener('change', () => { upi = sel.value; page = 1; loadTxns(); });
  $('#filters', root).appendChild(sel);

  async function loadCards() {
    const d = await api('/upis');
    upis = d.items;
    sel.innerHTML = '<option value="all">All UPI IDs</option>' + upis.map((u) => `<option value="${esc(u.id)}" ${u.id === upi ? 'selected' : ''}>${esc(u.name)} · ${esc(u.upi_id)}</option>`).join('');
    const tot = (k) => upis.reduce((s, u) => s + (u[k] || 0), 0);
    $('#cards', root).innerHTML = (upis.length ? tileHtml('All UPI · today', fmtMoney(tot('today')), `yesterday ${fmtMoney(tot('yesterday'))}`, { big: true, drill: 'all' }) : '') +
      upis.map((u) => `<div class="card upi-card ${u.id === upi ? 'sel' : ''}" data-upi="${esc(u.id)}" tabindex="0">
        <div class="row">${u.scanner_url ? `<img class="qr" src="${esc(u.scanner_url)}" alt="QR">` : '<div class="avatar lg">🏦</div>'}
          <div class="grow"><b>${esc(u.name)}</b><br><span class="mono tiny">${esc(u.upi_id)}</span><br><small class="muted">owner chat <span class="mono">${esc(u.telegram_chat_id)}</span></small></div>
          <span class="badge ${u.active ? 'ok' : 'bad'}">${u.active ? 'active' : 'off'}</span></div>
        <div class="app-card"><div class="stats" style="grid-template-columns:repeat(3,1fr)"><div><small>Today</small><b>${fmtMoney(u.today)}</b></div><div><small>Yesterday</small><b>${fmtMoney(u.yesterday)}</b></div><div><small>All time</small><b>${fmtMoney(u.all)}</b></div></div></div>
        <div class="row gap-top"><button class="btn sm" data-act="pay" data-id="${esc(u.id)}">💰 Payments</button><button class="btn sm w-only" data-act="edit" data-id="${esc(u.id)}">✏️ Edit</button><button class="btn sm danger w-only del-only" data-act="del" data-id="${esc(u.id)}">🗑</button></div></div>`).join('') ||
      empty('🏦', 'Add your first UPI ID to start selling.');
  }

  function payments(id, name) {
    let r = { range: 'today', start: '', end: '' };
    const m = drill({ title: `💰 Payments · ${name}`, header: (d) => `<div id="pb"></div><div class="kpi-row gap-top">${tileHtml('Received', fmtMoney(d.amount_total), `${d.count} payment${d.count === 1 ? '' : 's'} · ${rangeLabel(r.range)}`, { big: true })}</div>`,
      load: (p) => api(`/upis/${id}/stats`, { params: { ...r, page: p } }),
      row: (e) => `<div class="li" data-chat="${esc(e.user.chat_id)}">${avatar(e.user.profile_image_url, e.user.full_name, 'lg')}<div class="meta"><b>${esc(e.user.full_name || '—')}</b>
        <small><span class="mono">${esc(e.user.chat_id)}</span> · ${esc(e.app_name || 'Wallet deposit')} · ${fmtDate(e.ts)}</small></div><div class="acts"><b>${fmtMoney(e.amount)}</b></div></div>` });
    m.el.addEventListener('click', (e) => { const c = e.target.closest('[data-chat]'); if (c) userDetail(c.dataset.chat); });
    return m;
  }

  async function loadTxns() {
    $('#list', root).innerHTML = skeleton(3);
    try {
      const d = await api('/txns', { params: { status, upi_id: upi, q, page, ...rng } });
      rows = d.items;
      tabs.setTabs([['pending', '⏳ Pending', d.counts.pending], ['verification', '🕒 In verification', d.counts.verification], ['failed', '❌ Failed'], ['success', '✅ Success']]);
      $('#list', root).innerHTML = (d.searching ? `<p class="tiny muted">Search results across all statuses · ${d.total} found</p>` : `<p class="tiny muted">${d.total} transaction${d.total === 1 ? '' : 's'} · ${fmtMoney(d.total_amount)}</p>`) +
        (rows.length ? rows.map(txnRow).join('') : empty('🧾', 'No transactions here.'));
      pager($('#pg', root), d, (p) => { page = p; loadTxns(); });
    } catch (e) { toast(e.message, 'err'); }
  }

  function txnRow(t, i) {
    const b = { pending: 'warn', verification: 'info', failed: 'bad', success: 'ok' }[t.status];
    return `<div class="li wide" data-i="${i}">${avatar(t.user.profile_image_url, t.user.full_name, 'lg')}
      <div class="meta"><b>${esc(t.user.full_name || '—')} <span class="badge ${b}">${t.status === 'verification' ? 'In verification' : t.status}</span></b>
        <small>${t.user.username ? '@' + esc(t.user.username) + ' · ' : ''}<span class="mono">${esc(t.user.chat_id)}</span></small>
        <div class="chips"><span class="chip mono" data-copy-text="${esc(t.id)}">🧾 ${esc(t.id)}</span><span class="badge violet">📱 ${esc(t.app_name)} ${esc(t.plan_label)}</span><span class="badge">🏦 ${esc(t.upi_id)}</span>
          ${t.utr ? `<span class="chip mono" data-copy-text="${esc(t.utr)}">UTR ${esc(t.utr)}</span>` : ''}${t.wallet_used ? `<span class="badge ok">👛 wallet ${fmtMoney(t.wallet_used)}</span>` : ''}
          ${t.license_key ? `<span class="chip mono" data-copy-text="${esc(t.license_key)}">🔑 ${esc(t.license_key)}</span>` : ''}</div>
        ${t.fail_reason ? `<small class="neg">${esc(t.fail_reason)}</small>` : ''}<small class="muted">${fmtDate(t.created_at)}${t.verified_by ? ' · by ' + esc(t.verified_by) : ''}</small></div>
      <div class="acts"><b>${fmtMoney(t.amount)}</b>${t.status === 'pending' ? `<span class="badge warn mono" data-exp="${t.expires_at}">${mmss(t.remaining)}</span>` : ''}
        ${t.screenshot_url ? `<img class="shot" src="${esc(t.screenshot_url)}" alt="screenshot" data-shot="${esc(t.screenshot_url)}">` : ''}
        ${['verification', 'pending'].includes(t.status) ? `<button class="btn sm ok w-only" data-act="verify" data-i="${i}">✅ Verify</button><button class="btn sm danger w-only" data-act="reject" data-i="${i}">❌ Reject</button>` : ''}</div></div>`;
  }

  tick(() => $$('[data-exp]', root).forEach((el) => { el.textContent = mmss(+el.dataset.exp - Date.now() / 1000); }), 1000);

  function upiForm(u = {}) {
    const edit = !!u.id;
    const m = openModal({ title: edit ? 'Edit UPI ID' : 'Add UPI ID', body: `${fld('Name', `<input name="name" required maxlength="60" value="${esc(u.name || '')}">`)}
      ${fld('UPI ID', `<input name="upi_id" required maxlength="80" placeholder="name@bank" value="${esc(u.upi_id || '')}">`)}
      ${fld('Owner Telegram chat ID', `<input name="telegram_chat_id" required inputmode="numeric" value="${esc(u.telegram_chat_id || '')}">`, 'The owner verifies payments inside the bot and sees “My Payment Collection”.')}
      ${fld(edit ? 'New scanner image (optional)' : 'Scanner (QR) image', `<input type="file" name="scanner" accept="image/*" ${edit ? '' : 'required'}>`, 'Used as a fallback only — the bot generates a fresh QR with the amount filled in.')}
      ${edit ? `<label class="row"><span class="switch"><input type="checkbox" name="active" value="1" ${u.active ? 'checked' : ''}><i></i></span> Active</label>` : ''}`,
      submit: edit ? 'Save' : 'Add',
      onSubmit: async (_d, form) => { const fd = new FormData(form); await api(edit ? `/upis/${u.id}` : '/upis', { method: 'POST', form: fd }); toast('Saved ✨'); loadCards(); } });
    return m;
  }

  root.addEventListener('click', async (e) => {
    if (e.target.closest('[data-copy-text]')) return;
    const shot = e.target.closest('[data-shot]'); if (shot) return lightbox(shot.dataset.shot);
    const act = e.target.closest('[data-act]');
    if (act) {
      const u = upis.find((x) => x.id === act.dataset.id), t = rows[+act.dataset.i];
      if (act.dataset.act === 'pay') return payments(u.id, u.name);
      if (act.dataset.act === 'edit') return upiForm(u);
      if (act.dataset.act === 'del' && await confirmBox('Delete UPI ID?', `${esc(u.upi_id)} stops receiving payments. History is kept.`, 'Delete')) { await api(`/upis/${u.id}`, { method: 'DELETE' }); loadCards(); }
      if (act.dataset.act === 'verify' && await confirmBox('Verify payment?', `Confirm ${fmtMoney(t.amount)} was received from ${esc(t.user.full_name)}. The key is delivered instantly.`, 'Verify', false)) {
        try { await api(`/txns/${t.id}/verify`, { method: 'POST' }); toast('Verified — key delivered 🎉'); loadTxns(); loadCards(); } catch (er) { toast(er.message, 'err'); }
      }
      if (act.dataset.act === 'reject') openModal({ title: 'Reject payment', danger: true, submit: 'Reject', body: fld('Reason (sent to the user)', '<textarea name="reason" required maxlength="300" rows="3"></textarea>'),
        onSubmit: async (d) => { await api(`/txns/${t.id}/reject`, { method: 'POST', body: { reason: d.reason } }); toast('Rejected'); loadTxns(); } });
      return;
    }
    const card = e.target.closest('[data-upi]');
    if (card && !e.target.closest('button')) { upi = upi === card.dataset.upi ? 'all' : card.dataset.upi; sel.value = upi; $$('.upi-card', root).forEach((c) => c.classList.toggle('sel', c.dataset.upi === upi)); page = 1; loadTxns(); }
  });
  $('#addUpi', root).addEventListener('click', () => upiForm());
  bindDrill($('#cards', root), (k) => { if (k === 'all') payments('all', 'All UPI IDs'); });
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); page = 1; loadTxns(); }));
  await loadCards();
  loadTxns();
}
