// Sellers (admin): earnings & sign-ups (today / yesterday / all / custom) with drill-down, apps waiting for review, sellers list,
// seller withdrawals, platform settings.
import { $, api, avatar, bindDrill, confirmBox, debounce, drill, empty, esc, fld, fmtDate, fmtMoney, fmtNum, lightbox, openModal, pager, rangeBlocks, rangeLabel, skeleton, tabsBar, tileHtml, toast } from '../core.js';

const SUB_BADGE = { pending: 'warn', approved: 'ok', rejected: 'bad' };

export function sellerForm(s = {}, done) {
  const edit = !!s.id;
  return openModal({ title: edit ? `Edit ${s.name}` : 'Add seller', wide: true, submit: edit ? 'Save' : 'Create',
    body: `<div class="two">${fld('Name', `<input name="name" required maxlength="60" value="${esc(s.name || '')}">`)}${fld('Telegram @username', `<input name="username" maxlength="40" value="${esc(s.username || '')}">`)}</div>
      <div class="two">${fld('Chat ID', `<input name="chat_id" inputmode="numeric" value="${esc(s.chat_id || '')}">`, 'The seller can also link Telegram themselves from their panel.')}${fld('Profile image', '<input type="file" name="avatar" accept="image/*">')}</div>
      <div class="two">${fld('Login username', `<input name="login_username" required maxlength="32" autocomplete="off" value="${esc(s.login_username || '')}">`)}${fld(edit ? 'New password (optional)' : 'Password', `<input name="password" type="password" minlength="10" autocomplete="new-password" ${edit ? '' : 'required'}>`)}</div>
      ${fld('Note', `<input name="note" maxlength="300" value="${esc(s.note || '')}">`)}
      ${edit ? `<label class="row"><span class="switch"><input type="checkbox" name="active" value="1" ${s.active ? 'checked' : ''}><i></i></span> Active (can log in)</label>` : ''}`,
    onSubmit: async (_d, form) => { const fd = new FormData(form); if (edit && !fd.has('active')) fd.set('active', '0'); await api(edit ? `/sellers/${s.id}` : '/sellers', { method: 'POST', form: fd }); toast('Saved ✨'); done(); } });
}

function drillEarnings(range, rng) {
  drill({ title: `🚀 Seller earnings · ${rangeLabel(range)}`, header: (d) => `<div class="kpi-row">${tileHtml('Paid to sellers', fmtMoney(d.amount_total), `${fmtNum(d.count)} sales`, { big: true })}</div>`,
    load: (p) => api('/sellers/earnings', { params: { range, ...rng, page: p } }),
    row: (e) => `<div class="li wide"><div class="avatar">💰</div><div class="meta"><b>${esc(e.seller_name)}</b><small>${esc(e.app_name)} · ${esc(e.plan_label)} · buyer ${esc(e.user.full_name)} (<span class="mono">${esc(e.user.chat_id)}</span>) · ${fmtDate(e.ts)}</small></div><div class="acts"><b>${fmtMoney(e.amount)}</b><small class="muted">list ${fmtMoney(e.gross)}</small></div></div>` });
}
function drillSignups(range, rng) {
  const m = drill({ title: `🆕 Seller sign-ups · ${rangeLabel(range)}`, load: (p) => api('/sellers/signups', { params: { range, ...rng, page: p } }),
    row: (s) => `<div class="li wide" data-go="#/seller/${esc(s.id)}" tabindex="0">${avatar(s.profile_image_url, s.name, 'lg')}<div class="meta"><b>${esc(s.name)}</b><small>${fmtDate(s.created_at)}</small></div></div>` });
  m.el.addEventListener('click', (e) => { const g = e.target.closest('[data-go]'); if (g) { m.close(); location.hash = g.dataset.go; } });
}

export default async function (root) {
  let tab = 'sellers', page = 1, q = '', wStatus = 'pending', subStatus = 'pending', wItems = [], cfg;
  root.innerHTML = `<div class="page-head"><div><h1>🚀 Seller</h1><p>Sellers publish apps and earn on every sale. You test and approve every app.</p></div><div class="row"><button class="btn" id="cfgBtn">⚙️ Seller settings</button><button class="btn primary w-only" id="add">＋ Add seller</button></div></div>
    <div id="blocks"></div><div id="tiles" class="grid cols-4 keep2 gap-top"></div>
    <div class="row between gap-top"><div id="tb"></div><label class="search grow" id="sb"><span>🔎</span><input id="q" type="search" placeholder="Name, @username, chat id" maxlength="64"></label></div>
    <div id="main" class="gap-top">${skeleton(3)}</div><div id="pg"></div>`;
  const tb = tabsBar([['sellers', '👥 Sellers'], ['review', '🧪 Apps in verification'], ['wd', '💸 Withdrawals']], tab, (k) => { tab = k; page = 1; $('#sb', root).hidden = k !== 'sellers'; load(); });
  $('#tb', root).appendChild(tb.el);

  async function load() {
    const box = $('#main', root); box.innerHTML = skeleton(3);
    try {
      if (tab === 'sellers') {
        const d = await api('/sellers', { params: { q, page } });
        box.innerHTML = `<div class="list">${d.items.length ? d.items.map((s) => `<div class="li wide" data-id="${esc(s.id)}" tabindex="0">${avatar(s.profile_image_url, s.name, 'lg')}
          <div class="meta"><b>${esc(s.name)} ${s.active ? '' : '<span class="badge bad">disabled</span>'}</b><small>${s.username ? '@' + esc(s.username) + ' · ' : ''}<span class="mono">${esc(s.chat_id || 'no chat id')}</span></small>
          <div class="chips"><span class="badge violet">📱 ${s.apps_published} published</span><span class="badge">${fmtNum(s.sales_count)} sells</span><span class="badge ok">balance ${fmtMoney(s.balance)}</span></div></div><div class="acts"><b>${fmtMoney(s.total_earned)}</b><small class="muted">total earned</small></div></div>`).join('') : empty('🚀', 'No sellers yet.')}</div>`;
        pager($('#pg', root), d, (p) => { page = p; load(); });
      } else if (tab === 'review') {
        const d = await api('/submissions', { params: { status: subStatus, page } });
        box.innerHTML = `<div class="seg" id="sseg">${['pending', 'approved', 'rejected'].map((s) => `<button data-s="${s}" class="${s === subStatus ? 'on' : ''}">${s}</button>`).join('')}</div><div class="list gap-top">${d.items.length ? d.items.map((s) => `<div class="li wide" data-sub="${esc(s.id)}" tabindex="0">${avatar(s.image_url, s.app_name, 'lg')}
          <div class="meta"><b>${esc(s.app_name)} <span class="badge ${s.kind === 'new' ? 'violet' : 'info'}">${s.kind === 'new' ? 'new app' : 'update'}</span></b><small>🚀 ${esc(s.seller_name)} · ${fmtDate(s.created_at)}</small>${s.reason ? `<small class="neg">${esc(s.reason)}</small>` : ''}</div>
          <div class="acts"><span class="badge ${SUB_BADGE[s.status]}">${s.status}</span>${s.status === 'pending' ? `<span class="badge">${s.checks_passed}/${s.checks_required} checks</span>` : ''}</div></div>`).join('') : empty('🧪', 'Nothing here.')}</div>`;
        pager($('#pg', root), d, (p) => { page = p; load(); });
      } else {
        const d = await api('/seller-withdrawals', { params: { status: wStatus, page } });
        wItems = d.items;
        box.innerHTML = `<div class="seg" id="wseg">${['pending', 'approved', 'rejected'].map((s) => `<button data-s="${s}" class="${s === wStatus ? 'on' : ''}">${s}</button>`).join('')}</div><div class="list gap-top">${d.items.length ? d.items.map((w, i) => `<div class="li wide"><div class="avatar">💸</div>
          <div class="meta"><b>${esc(w.seller_name)} → ${esc(w.name)}</b><small>UPI <span class="mono">${esc(w.upi_id)}</span> · ${fmtDate(w.created_at)} · fee ${w.fee_pct}% (${fmtMoney(w.fee)})</small>${w.reason ? `<small>${esc(w.reason)}</small>` : ''}</div>
          <div class="acts"><b>${fmtMoney(w.amount)}</b><span class="badge ok">pay ${fmtMoney(w.receive)}</span><span class="badge ${SUB_BADGE[w.status === 'approved' ? 'approved' : w.status]}">${w.status}</span>${w.proof_url ? `<img class="shot" src="${esc(w.proof_url)}" data-shot="${esc(w.proof_url)}" alt="">` : ''}
          ${w.status === 'pending' ? `<button class="btn sm ok w-only" data-w="approve" data-i="${i}">Approve</button><button class="btn sm danger w-only" data-w="reject" data-i="${i}">Reject</button>` : ''}</div></div>`).join('') : empty('💸', 'No withdrawals.')}</div>`;
        pager($('#pg', root), d, (p) => { page = p; load(); });
      }
    } catch (e) { toast(e.message, 'err'); }
  }

  async function tiles() {
    const d = await api('/sellers/overview'); cfg = d.settings;
    $('#tiles', root).innerHTML = tileHtml('Sellers', fmtNum(d.total_sellers), 'registered', { big: true }) + tileHtml('Apps in verification', fmtNum(d.pending_submissions), 'tap to review', { hot: d.pending_submissions > 0, drill: 'review' }) +
      tileHtml('Withdraw requests', fmtNum(d.pending_withdrawals), fmtMoney(d.pending_withdraw_amount), { drill: 'wd' }) + tileHtml('File storage', d.storage_configured ? '✅ Appwrite' : '⚠️ not set', d.storage_configured ? 'large APKs OK' : 'APK > 45 MB blocked');
    tb.setTabs([['sellers', '👥 Sellers'], ['review', '🧪 Apps in verification', d.pending_submissions], ['wd', '💸 Withdrawals', d.pending_withdrawals]]);
  }
  bindDrill($('#tiles', root), (k) => { tab = k; tb.set(k); page = 1; $('#sb', root).hidden = true; load(); });

  root.addEventListener('click', (e) => {
    const shot = e.target.closest('[data-shot]'); if (shot) return lightbox(shot.dataset.shot);
    const s = e.target.closest('[data-id]'); if (s) return (location.hash = '#/seller/' + s.dataset.id);
    const sub = e.target.closest('[data-sub]'); if (sub) return (location.hash = '#/seller/review/' + sub.dataset.sub);
    const sg = e.target.closest('#sseg [data-s]'); if (sg) { subStatus = sg.dataset.s; page = 1; return load(); }
    const wg = e.target.closest('#wseg [data-s]'); if (wg) { wStatus = wg.dataset.s; page = 1; return load(); }
    const w = e.target.closest('[data-w]'); if (w) { const it = wItems[+w.dataset.i], approve = w.dataset.w === 'approve';
      openModal({ title: approve ? `Approve ${fmtMoney(it.amount)}` : 'Reject withdrawal', danger: !approve, submit: approve ? 'Approve' : 'Reject',
        body: `<p class="muted">Pay <b>${fmtMoney(it.receive)}</b> (after ${it.fee_pct}% fee) to <span class="mono">${esc(it.upi_id)}</span> — ${esc(it.name)}.</p>${fld(approve ? 'Note (optional)' : 'Reason (sent to the seller)', `<textarea name="reason" rows="2" maxlength="300" ${approve ? '' : 'required'}></textarea>`)}${fld('Payment proof (optional)', '<input type="file" name="photo" accept="image/*">')}`,
        onSubmit: async (_d, form) => { const fd = new FormData(form); fd.append('action', approve ? 'approve' : 'reject'); await api(`/seller-withdrawals/${it.id}/resolve`, { method: 'POST', form: fd }); toast('Done — the seller was notified'); tiles(); load(); } }); }
  });
  $('#add', root).onclick = () => sellerForm({}, () => { tiles(); load(); });
  $('#cfgBtn', root).onclick = () => openModal({ title: '⚙️ Seller settings', body: `<div class="two">${fld('Minimum withdrawal ₹', `<input name="min_withdraw" type="number" min="1" value="${cfg.min_withdraw}" required>`)}${fld('Withdraw fee %', `<input name="fee_pct" type="number" step="0.1" min="0" max="50" value="${cfg.fee_pct}" required>`)}</div>
      <div class="two">${fld('Platform commission % on sales', `<input name="commission_pct" type="number" step="0.1" min="0" max="90" value="${cfg.commission_pct}" required>`, 'Taken from the plan price before crediting the seller. 0 = seller keeps 100%.')}${fld('Max APK size (MB)', `<input name="max_apk_mb" type="number" min="10" max="200" value="${cfg.max_apk_mb}" required>`)}</div>
      <label class="row"><span class="switch"><input type="checkbox" name="signup_open" ${cfg.signup_open ? 'checked' : ''}><i></i></span> Allow new seller sign-ups</label>`,
    onSubmit: async (_d, form) => { const f = form.elements; await api('/settings/sellers', { method: 'PUT', body: { min_withdraw: f.min_withdraw.value, fee_pct: f.fee_pct.value, commission_pct: f.commission_pct.value, max_apk_mb: f.max_apk_mb.value, signup_open: f.signup_open.checked } }); toast('Saved ✨'); tiles(); } });
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); page = 1; load(); }));
  await tiles(); load();
  await rangeBlocks($('#blocks', root), {
    fetch: (start, end) => api('/sellers/overview', { params: { start, end } }),
    build: (d) => [{ title: '💸 Seller earnings (paid to sellers)', key: 'earn', data: Object.fromEntries(Object.entries(d.totals).map(([k, v]) => [k, v.earned])), fmt: fmtMoney, sub: Object.fromEntries(Object.entries(d.totals).map(([k, v]) => [k, `${fmtNum(v.sales)} sales`])) },
      { title: '🆕 Seller sign-ups', key: 'signup', data: Object.fromEntries(Object.entries(d.totals).map(([k, v]) => [k, v.signups])), fmt: fmtNum }],
    onDrill: (k, range, rng) => (k === 'earn' ? drillEarnings(range, rng) : drillSignups(range, rng)),
  });
}
