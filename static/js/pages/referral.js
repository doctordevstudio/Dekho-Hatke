// Refer & Earn (admin + reseller): overview, default % and per-user override, referrers, their referrals and plans, withdrawals.
import { $, api, avatar, debounce, drill, empty, esc, fld, fmtDate, fmtMoney, fmtNum, lightbox, openModal, pager, richEditor, skeleton, state, tabsBar, tileHtml, toast, bindDrill } from '../core.js';
import { userDetail } from './userui.js';

export default async function (root) {
  const admin = state.panel === 'admin';
  let tab = 'users', page = 1, q = '', wStatus = 'pending', ov;
  root.innerHTML = `<div class="page-head"><div><h1>🎁 Referral</h1><p>Referrers earn a lifetime commission on every purchase their friends make. ${admin ? '' : 'This is your own store’s referral system.'}</p></div></div>
    <div id="tiles" class="grid cols-4 keep2">${skeleton(1)}</div>
    <div class="grid cols-2 gap-top"><div class="card" id="cfg"></div>${admin ? '<div class="card" id="msg"></div>' : ''}</div>
    <div class="row between gap-top"><div id="tb"></div><label class="search grow" id="sb"><span>🔎</span><input id="q" type="search" placeholder="Search referrer" maxlength="64"></label></div>
    <div id="list" class="list gap-top">${skeleton(3)}</div><div id="pg"></div>`;
  const tb = tabsBar([['users', '👥 Referrers'], ['wd', '💸 Withdrawals']], tab, (k) => { tab = k; page = 1; $('#sb', root).hidden = k !== 'users'; load(); });
  $('#tb', root).appendChild(tb.el);

  async function loadOverview() {
    const d = await api('/referral');
    ov = d.overview;
    $('#tiles', root).innerHTML = tileHtml('Referrers', fmtNum(ov.referrers), 'people with referrals', { big: true }) + tileHtml('Referred users', fmtNum(ov.registered), `${fmtNum(ov.converted)} bought · ${fmtNum(ov.pending)} not yet`) +
      tileHtml('Commission paid', fmtMoney(ov.earned), 'lifetime', { hot: true }) + tileHtml('Pending withdrawals', fmtNum(ov.pending_withdrawals), fmtMoney(ov.pending_withdraw_amount), { drill: 'wd' });
    $('#cfg', root).innerHTML = `<h3>⚙️ Settings</h3><form id="cfgForm" class="stack">${fld('Default commission % (all users)', `<input name="percent" type="number" step="0.1" min="0" max="90" value="${ov.default_percent}" required>`, 'Per-user overrides below take priority.')}
      ${fld('Minimum withdrawal (₹)', `<input name="min_withdraw" type="number" min="1" value="${ov.min_withdraw}" required>`)}<button class="btn primary w-only">Save settings</button></form>`;
    $('#cfgForm', root).onsubmit = async (e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); try { await api('/referral/settings', { method: 'PUT', body: f }); toast('Saved ✨'); loadOverview(); } catch (er) { toast(er.message, 'err'); } };
    if (admin && d.message) {
      const ed = richEditor({ ...d.message, label: 'Message shown when a user taps “Refer & Earn”', hint: 'Placeholders: {percent} {link} {count} {earned}. HTML or plain text, optional image.' });
      const box = $('#msg', root); box.innerHTML = '<h3>💬 Refer message</h3>'; box.appendChild(ed.el);
      const b = document.createElement('button'); b.className = 'btn primary gap-top w-only'; b.textContent = 'Save message';
      b.onclick = async () => { try { await api('/referral/message', { method: 'PUT', body: ed.value() }); toast('Message saved ✨'); } catch (er) { toast(er.message, 'err'); } };
      box.appendChild(b);
    }
    tb.setTabs([['users', '👥 Referrers'], ['wd', '💸 Withdrawals', ov.pending_withdrawals]]);
  }

  async function load() {
    $('#list', root).innerHTML = skeleton(3);
    try {
      if (tab === 'users') {
        const d = await api('/referral/users', { params: { q, page } });
        $('#list', root).innerHTML = d.items.length ? d.items.map((r) => `<div class="li" data-chat="${esc(r.chat_id)}" tabindex="0">${avatar(r.profile_image_url, r.full_name, 'lg')}
          <div class="meta"><b>${esc(r.full_name || '—')}</b><small>${r.username ? '@' + esc(r.username) + ' · ' : ''}<span class="mono">${esc(r.chat_id)}</span></small>
          <div class="chips"><span class="badge info">👥 ${r.count} referred</span><span class="badge ok">🛒 ${r.converted} bought</span><span class="badge violet">${r.percent}%${r.custom_percent != null ? ' (custom)' : ''}</span></div></div>
          <div class="acts"><b>${fmtMoney(r.earned)}</b><small class="muted">balance ${fmtMoney(r.balance)}</small></div></div>`).join('') : empty('🎁', 'Nobody has referred anyone yet.');
        pager($('#pg', root), d, (p) => { page = p; load(); });
      } else {
        const d = await api('/referral/withdrawals', { params: { status: wStatus, page } });
        $('#list', root).innerHTML = `<div class="seg gap-top" id="wseg">${['pending', 'approved', 'rejected'].map((s) => `<button data-s="${s}" class="${s === wStatus ? 'on' : ''}">${s}</button>`).join('')}</div>` +
          (d.items.length ? d.items.map((w, i) => `<div class="li wide" data-w="${i}">${avatar(w.user.profile_image_url, w.user.full_name, 'lg')}
            <div class="meta"><b>${esc(w.user.full_name || '—')}</b><small><span class="mono">${esc(w.user.chat_id)}</span> · UPI <span class="mono">${esc(w.upi_id)}</span> · ${fmtDate(w.created_at)}</small>
            ${w.reason ? `<small>${esc(w.reason)}</small>` : ''}</div><div class="acts"><b>${fmtMoney(w.amount)}</b><span class="badge ${{ pending: 'warn', approved: 'ok', rejected: 'bad' }[w.status]}">${w.status}</span>
            ${w.proof_url ? `<img class="shot" src="${esc(w.proof_url)}" data-shot="${esc(w.proof_url)}" alt="proof">` : ''}
            ${w.status === 'pending' ? `<button class="btn sm ok w-only" data-r="approve" data-i="${i}">Approve</button><button class="btn sm danger w-only" data-r="reject" data-i="${i}">Reject</button>` : ''}</div></div>`).join('') : empty('💸', 'No withdrawals.'));
        $('#list', root).dataset.items = '';
        window._wd = d.items;
        pager($('#pg', root), d, (p) => { page = p; load(); });
      }
    } catch (e) { toast(e.message, 'err'); }
  }

  function resolve(w, action) {
    openModal({ title: action === 'approve' ? `Approve ${fmtMoney(w.amount)}` : 'Reject withdrawal', danger: action === 'reject', submit: action === 'approve' ? 'Approve' : 'Reject',
      body: `<p class="muted">Pay <b>${fmtMoney(w.amount)}</b> to <span class="mono">${esc(w.upi_id)}</span> (${esc(w.user.full_name)}).</p>
        ${fld(action === 'reject' ? 'Reason (sent to the user)' : 'Note (optional)', `<textarea name="reason" rows="2" maxlength="300" ${action === 'reject' ? 'required' : ''}></textarea>`)}
        ${fld('Payment proof screenshot (optional)', '<input type="file" name="photo" accept="image/*">')}`,
      onSubmit: async (_d, form) => { const fd = new FormData(form); fd.append('action', action); await api(`/referral/withdrawals/${w.id}/resolve`, { method: 'POST', form: fd }); toast('Done — the user was notified'); loadOverview(); load(); } });
  }

  async function referrerModal(chat) {
    const d = await api(`/referral/users/${encodeURIComponent(chat)}`);
    const m = openModal({ title: `🎁 ${d.user.full_name || chat}`, wide: true, noFoot: true, body: `
      <div class="grid cols-3 keep2"><div class="tile"><div class="lbl">Referred</div><div class="val">${d.stats.count || 0}</div></div><div class="tile"><div class="lbl">Bought</div><div class="val">${d.stats.converted || 0}</div></div><div class="tile hot"><div class="lbl">Earned</div><div class="val">${fmtMoney(d.stats.earned || 0)}</div></div></div>
      <form class="row gap-top" id="pf">${fld('Custom commission % (empty = default ' + d.default_percent + '%)', `<input name="percent" type="number" step="0.1" min="0" max="90" value="${d.custom_percent ?? ''}">`)}<button class="btn primary w-only">Save %</button><button type="button" class="btn" id="usr">User details</button></form>
      <div class="section-title"><h2>Referred users</h2></div><div class="list">${d.referrals.length ? d.referrals.map((r) => `<div class="li wide">${avatar(r.profile_image_url, r.full_name, 'lg')}<div class="meta"><b>${esc(r.full_name || '—')}</b>
        <small>joined ${fmtDate(r.joined_at)} · <span class="badge ${r.status === 'converted' ? 'ok' : 'warn'}">${r.status === 'converted' ? 'bought' : 'not yet'}</span></small>
        ${r.purchases.map((p) => `<div class="chips"><span class="badge violet">📱 ${esc(p.app_name)}</span><span class="badge">${esc(p.plan_label)} · paid ${fmtMoney(p.paid)}</span><span class="badge ok">+${fmtMoney(p.commission)} (${p.percent}%)</span><small class="muted">${fmtDate(p.ts)}</small></div>`).join('')}</div>
        <div class="acts"><b>${fmtMoney(r.earned)}</b></div></div>`).join('') : empty('🫥', 'No referrals yet.')}</div>` });
    $('#pf', m.el).onsubmit = async (e) => { e.preventDefault(); try { await api(`/referral/users/${encodeURIComponent(chat)}/percent`, { method: 'PUT', body: { percent: new FormData(e.target).get('percent') } }); toast('Saved ✨'); load(); } catch (er) { toast(er.message, 'err'); } };
    $('#usr', m.el).onclick = () => userDetail(chat);
  }

  root.addEventListener('click', (e) => {
    const shot = e.target.closest('[data-shot]'); if (shot) return lightbox(shot.dataset.shot);
    const s = e.target.closest('#wseg [data-s]'); if (s) { wStatus = s.dataset.s; page = 1; return load(); }
    const r = e.target.closest('[data-r]'); if (r) return resolve(window._wd[+r.dataset.i], r.dataset.r);
    const row = e.target.closest('[data-chat]'); if (row) referrerModal(row.dataset.chat).catch((er) => toast(er.message, 'err'));
  });
  bindDrill($('#tiles', root), (k) => { if (k === 'wd') { tab = 'wd'; tb.set('wd'); page = 1; $('#sb', root).hidden = true; load(); } });
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); page = 1; load(); }));
  await loadOverview();
  load();
}
