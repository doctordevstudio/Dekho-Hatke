import { $, $$, api, avatar, confirmBox, copyText, debounce, empty, esc, fmtDate, fmtMoney, fmtNum, lightbox, mmss, openModal, pager, skeleton, statusBadge, tick, toast, state } from './core.js';

const STATUSES = [['pending', '⏳ Pending'], ['verification', '🕒 In verification'], ['failed', '❌ Failed'], ['success', '✅ Success']];

function upiForm(u) {
  return `<label class="field"><span>Name</span><input type="text" name="name" maxlength="60" required value="${esc(u?.name || '')}" placeholder="e.g. Shop Account"></label>
    <label class="field"><span>UPI ID</span><input type="text" name="upi_id" maxlength="80" required value="${esc(u?.upi_id || '')}" placeholder="name@bank" autocapitalize="off"></label>
    <label class="field"><span>Telegram chat ID (gets payment alerts)</span><input type="text" name="telegram_chat_id" inputmode="numeric" required value="${esc(u?.telegram_chat_id || '')}" placeholder="123456789"></label>
    <label class="field"><span>Scanner / QR image ${u ? '(leave empty to keep current)' : ''}</span><input type="file" name="scanner" accept="image/png,image/jpeg,image/webp" ${u ? '' : 'required'}></label>
    ${u ? `<label class="row"><span class="switch"><input type="checkbox" name="active_cb" ${u.active ? 'checked' : ''}><i></i></span><span>Active (used for new payments)</span></label>` : ''}`;
}

function txnRow(t) {
  const u = t.user;
  const reason = t.status === 'failed' ? `<small class="muted">${t.fail_type === 'expired' ? '⌛ Expired after 10 minutes' : t.fail_type === 'cancelled' ? '🚫 Cancelled by user' : '📝 ' + esc(t.fail_reason)}</small>` : '';
  const acts = t.status === 'verification' ? `<button class="btn sm ok w-only" data-verify="${esc(t.id)}">✅ Verify</button><button class="btn sm danger w-only" data-reject="${esc(t.id)}">❌ Reject</button>` : '';
  return `<div class="li">${avatar(u.profile_image_url, u.full_name, 'lg')}
    <div class="meta"><b>${esc(u.full_name || '—')}</b><small>${u.username ? '@' + esc(u.username) : 'no username'} · <span class="mono">${esc(u.chat_id)}</span></small>
      <div class="chips"><span class="badge violet">📱 ${esc(t.app_name)}</span><span class="badge">${esc(t.plan_label)}</span><span class="badge ok">${fmtMoney(t.amount)}</span>
      <span class="chip mono" data-copy-text="${esc(t.id)}">🧾 ${esc(t.id)}</span>${t.utr ? `<span class="chip mono" data-copy-text="${esc(t.utr)}">UTR ${esc(t.utr)}</span>` : ''}
      <span class="badge">🏦 ${esc(t.upi_id)}</span>${t.license_key ? `<span class="chip mono" data-copy-text="${esc(t.license_key)}">🔑 ${esc(t.license_key)}</span>` : ''}</div>${reason}
      ${t.verified_by ? `<small class="muted">by ${esc(t.verified_by)}</small>` : ''}</div>
    <div class="acts">${statusBadge(t.status)}${t.status === 'pending' ? `<span class="badge warn mono" data-exp="${t.expires_at}">${mmss(t.remaining)}</span>` : ''}
      ${t.screenshot_url ? `<img class="shot" src="${esc(t.screenshot_url)}" alt="payment screenshot" data-shot="${esc(t.screenshot_url)}" loading="lazy">` : ''}
      ${acts}<small class="muted">${fmtDate(t.created_at)}</small></div></div>`;
}

export default async function (root) {
  let sel = 'all', status = 'pending', page = 1, q = '', rng = 'today', start = '', end = '';
  root.innerHTML = `<div class="page-head"><div><h1>🏦 UPI IDs</h1><p>Payments are split across active UPIs in round-robin.</p></div>
    <button class="btn primary w-only" id="addUpi">＋ Add UPI</button></div>
    <div id="cards" class="grid auto">${skeleton(1)}</div>
    <div class="section-title"><h2 id="statTitle">Received</h2><div class="seg" id="seg"><button data-r="today" class="on">Today</button><button data-r="yesterday">Yesterday</button><button data-r="all">All time</button><button data-r="custom">Custom</button></div></div>
    <div class="row" id="customRow" hidden><input type="date" id="from" aria-label="From"><input type="date" id="to" aria-label="To"><button class="btn primary sm" id="apply">Apply</button></div>
    <div id="stats" class="grid cols-2"></div>
    <div class="section-title"><h2>🧾 Transactions</h2><label class="search"><span>🔎</span><input id="q" type="search" placeholder="Search Txn ID or UTR number" maxlength="40"></label></div>
    <div class="tabs" id="tabs"></div><div id="list" class="list"></div><div id="pg"></div>`;
  let upis = [];

  async function loadCards() {
    const d = await api('/upis'); upis = d.items;
    const all = { id: 'all', name: 'All UPIs', upi_id: `${upis.length} account${upis.length === 1 ? '' : 's'}`, today: upis.reduce((s, u) => s + u.today, 0), yesterday: upis.reduce((s, u) => s + u.yesterday, 0), all: upis.reduce((s, u) => s + u.all, 0), active: true, isAll: true };
    $('#cards', root).innerHTML = [all, ...upis].map((u) => `<div class="card upi-card${u.id === sel ? ' sel' : ''}" data-upi="${esc(u.id)}">
      <div class="row">${u.isAll ? '<div class="avatar lg">∑</div>' : u.scanner_url ? `<img class="qr" src="${esc(u.scanner_url)}" alt="QR" data-shot="${esc(u.scanner_url)}">` : '<div class="avatar lg">🏦</div>'}
      <div class="grow"><h3>${esc(u.name)}</h3><small class="muted mono">${esc(u.upi_id)}</small></div>${u.isAll ? '' : `<span class="badge ${u.active ? 'ok' : 'bad'}">${u.active ? 'Active' : 'Off'}</span>`}</div>
      <div class="chips"><span class="badge">Today ${fmtMoney(u.today)}</span><span class="badge">Yday ${fmtMoney(u.yesterday)}</span><span class="badge ok">All ${fmtMoney(u.all)}</span></div>
      ${u.isAll ? '' : `<div class="row w-only"><button class="btn sm" data-edit="${esc(u.id)}">✏️ Edit</button><button class="btn sm danger" data-del="${esc(u.id)}">🗑 Delete</button></div>`}</div>`).join('');
  }

  async function loadStats() {
    try {
      const d = await api(`/upis/${sel}/stats`, { params: { range: rng, start, end } });
      const name = sel === 'all' ? 'All UPIs' : upis.find((u) => u.id === sel)?.name || '';
      $('#statTitle', root).textContent = `💰 Received · ${name}`;
      $('#stats', root).innerHTML = `<div class="tile big-total"><div class="lbl">Amount received</div><div class="val">${fmtMoney(d.total)}</div><div class="sub">${fmtNum(d.count)} payment${d.count === 1 ? '' : 's'}</div></div>
        <div class="card flat"><h3>From which app</h3><div class="list">${d.by_app.length ? d.by_app.map((a) => `<div class="row between"><span>📱 ${esc(a.app_name)}</span><span><b>${fmtMoney(a.amount)}</b> <small class="muted">${a.count}×</small></span></div>`).join('') : '<span class="muted">Nothing yet</span>'}</div></div>
        <div class="card flat" id="byUser"><h3>By which user</h3><div class="list">${d.by_user.length ? d.by_user.map((u) => `<div class="row between"><span class="row">${avatar(u.profile_image_url, u.full_name, 'sm')}<span>${esc(u.full_name)} <small class="muted">${u.username ? '@' + esc(u.username) : ''} · ${esc(u.chat_id)}</small></span></span><span><b>${fmtMoney(u.amount)}</b> <small class="muted">${u.count}×</small></span></div>`).join('') : '<span class="muted">Nothing yet</span>'}</div></div>`;
    } catch (e) { toast(e.message, 'err'); }
  }

  async function loadTxns() {
    try {
      const d = await api('/txns', { params: { status, upi_id: sel, q, page } });
      $('#tabs', root).innerHTML = q ? `<button class="on">🔎 Search results (all statuses)</button>` : STATUSES.map(([k, l]) => `<button data-s="${k}" class="${k === status ? 'on' : ''}">${l}${d.counts[k] ? `<span class="count">${d.counts[k]}</span>` : ''}</button>`).join('');
      $('#list', root).innerHTML = d.items.length ? d.items.map(txnRow).join('') : empty(q ? '🕵️' : '🫙', q ? 'No transaction matches that ID / UTR.' : 'Nothing here.');
      pager($('#pg', root), d, (p) => { page = p; loadTxns(); });
    } catch (e) { toast(e.message, 'err'); }
  }

  await loadCards(); loadStats(); loadTxns();

  $('#cards', root).addEventListener('click', async (e) => {
    const edit = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), shot = e.target.closest('[data-shot]');
    if (shot) return lightbox(shot.dataset.shot);
    if (edit) {
      const u = upis.find((x) => x.id === edit.dataset.edit);
      return openModal({ title: 'Edit UPI', body: upiForm(u), onSubmit: async (v, form) => {
        const fd = new FormData(form); fd.set('active', fd.get('active_cb') ? '1' : '0'); fd.delete('active_cb');
        await api('/upis/' + u.id, { method: 'POST', form: fd }); toast('UPI updated'); await loadCards(); } });
    }
    if (del) {
      if (!await confirmBox('Delete this UPI?', 'It stops receiving new payments. Old transactions stay in the database.', 'Delete')) return;
      await api('/upis/' + del.dataset.del, { method: 'DELETE' }); if (sel === del.dataset.del) sel = 'all';
      toast('UPI deleted'); await loadCards(); loadStats(); loadTxns(); return;
    }
    const card = e.target.closest('[data-upi]');
    if (card) { sel = card.dataset.upi; page = 1; $$('.upi-card', root).forEach((c) => c.classList.toggle('sel', c === card)); loadStats(); loadTxns(); }
  });
  $('#addUpi', root).addEventListener('click', () => openModal({ title: 'Add UPI ID', submit: 'Save UPI', body: upiForm(null) +
    '<p class="tiny muted">With 2 or more active UPIs, payments rotate between them (round-robin).</p>',
    onSubmit: async (v, form) => { await api('/upis', { method: 'POST', form: new FormData(form) }); toast('UPI added 🏦'); await loadCards(); } }));

  $('#seg', root).addEventListener('click', (e) => {
    const b = e.target.closest('[data-r]'); if (!b) return;
    $$('#seg button', root).forEach((x) => x.classList.toggle('on', x === b)); rng = b.dataset.r;
    $('#customRow', root).hidden = rng !== 'custom'; if (rng !== 'custom') loadStats();
  });
  $('#apply', root).addEventListener('click', () => { start = $('#from', root).value; end = $('#to', root).value; if (!start || !end) return toast('Pick both dates', 'err'); loadStats(); });
  $('#byUser', root);
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); page = 1; loadTxns(); }, 400));
  $('#tabs', root).addEventListener('click', (e) => { const b = e.target.closest('[data-s]'); if (b) { status = b.dataset.s; page = 1; loadTxns(); } });
  $('#list', root).addEventListener('click', async (e) => {
    const shot = e.target.closest('[data-shot]'), ver = e.target.closest('[data-verify]'), rej = e.target.closest('[data-reject]');
    if (shot) return lightbox(shot.dataset.shot);
    try {
      if (ver) {
        if (!await confirmBox('Verify this payment?', 'A license key is generated and sent to the user on Telegram.', 'Verify ✅', false)) return;
        const r = await api(`/txns/${ver.dataset.verify}/verify`, { method: 'POST' }); toast('Verified · key ' + r.license_key); loadTxns(); loadCards();
      } else if (rej) {
        openModal({ title: 'Reject payment', submit: 'Reject', danger: true,
          body: '<label class="field"><span>Reason (sent to the user)</span><input type="text" name="reason" maxlength="300" required placeholder="e.g. UTR not found"></label>',
          onSubmit: async (v) => { await api(`/txns/${rej.dataset.reject}/reject`, { method: 'POST', body: { reason: v.reason } }); toast('Payment rejected'); loadTxns(); } });
      }
    } catch (err) { toast(err.message, 'err'); }
  });

  // live countdown for pending payments + gentle auto-refresh of the active queue
  let expiredFired = false;
  tick(() => {
    const now = Date.now() / 1000;
    $$('[data-exp]', root).forEach((el) => {
      const left = +el.dataset.exp - now;
      el.textContent = left > 0 ? mmss(left) : 'expired';
      if (left <= 0 && !expiredFired && status === 'pending') { expiredFired = true; setTimeout(() => { expiredFired = false; loadTxns(); }, 1500); }
    });
  });
  tick(() => { if (!q && (status === 'pending' || status === 'verification') && !document.querySelector('.modal-back')) loadTxns(); }, 20000);
}
