// One reseller (admin): earnings by range with paginated sales, dues + settlement, edit.
import { $, api, avatar, confirmBox, empty, esc, fld, fmtDate, fmtMoney, fmtNum, openModal, pager, rangeBar, skeleton, tileHtml, toast } from '../core.js';
import { resellerForm } from './a_resellers.js';

export default async function (root, rid) {
  let rng = { range: 'today', start: '', end: '' }, page = 1;
  root.innerHTML = skeleton(3);
  async function load() {
    try {
      const d = await api(`/resellers/${rid}`, { params: { ...rng, page } });
      const r = d.reseller, due = r.due;
      root.innerHTML = `<div class="page-head"><div class="row">${avatar(r.avatar_url, r.name, 'lg')}<div><h1>${esc(r.name)}</h1><p>${r.username ? '@' + esc(r.username) + ' · ' : ''}<span class="mono">${esc(r.chat_id)}</span> · 🤖 ${r.bot_username ? '@' + esc(r.bot_username) : '—'} · login <b>${esc(r.login_username)}</b></p></div></div>
        <div class="row"><a class="btn" href="#/reseller">← Back</a><button class="btn w-only" id="edit">✏️ Edit</button><button class="btn danger w-only del-only" id="del">🗑</button></div></div>
        <div class="grid cols-4 keep2">${tileHtml('Today', fmtMoney(d.ranges.today.earned), `commission ${fmtMoney(d.ranges.today.commission)}`, { hot: true })}${tileHtml('Yesterday', fmtMoney(d.ranges.yesterday.earned), `commission ${fmtMoney(d.ranges.yesterday.commission)}`)}
          ${tileHtml('All time', fmtMoney(d.ranges.all.earned), `commission ${fmtMoney(d.ranges.all.commission)}`)}${tileHtml('Commission rate', r.commission_pct + '%', 'of every sale')}</div>
        <div class="card gap-top ${due.forced ? 'forced' : ''}"><div class="row between"><div><h3>🧾 Dues since last settlement</h3><p class="muted tiny">Earned <b>${fmtMoney(due.earned)}</b> · ${fmtNum(due.sales)} sales · commission due <b>${fmtMoney(due.commission)}</b>${due.limit ? ` · maintenance at ${fmtMoney(due.limit)}` : ''}</p>
          ${due.forced ? '<span class="badge bad">⛔ Store is paused until you settle</span>' : ''}</div><button class="btn ok w-only" id="settle">✅ Mark settled</button></div>
          ${d.settlements.length ? `<div class="list gap-top">${d.settlements.map((s) => `<div class="li"><div class="avatar">🧾</div><div class="meta"><b>Settled ${fmtMoney(s.commission)}</b><small>on ${fmtMoney(s.earned)} · ${fmtNum(s.sales)} sales · ${fmtDate(s.ts)} · ${esc(s.by)} ${esc(s.note || '')}</small></div></div>`).join('')}</div>` : ''}</div>
        <div class="section-title"><h2>Sales</h2><div id="rb"></div></div>
        <p class="tiny muted">${fmtNum(d.total)} sale${d.total === 1 ? '' : 's'} · ${fmtMoney(d.amount_total)}</p>
        <div class="list">${d.items.length ? d.items.map((e) => `<div class="li wide">${avatar(e.user.profile_image_url, e.user.full_name, 'lg')}<div class="meta"><b>${esc(e.user.full_name || '—')}</b><small>${esc(e.app_name)} ${esc(e.label)} · ${fmtDate(e.ts)}</small></div>
          <div class="acts"><b>${fmtMoney(e.amount)}</b><span class="badge warn">commission ${fmtMoney(e.commission)}</span></div></div>`).join('') : empty('🪙', 'No sales in this period.')}</div><div id="pg"></div>`;
      $('#rb', root).appendChild(rangeBar((x) => { rng = x; page = 1; load(); }, { initial: rng.range }).el);
      pager($('#pg', root), d, (p) => { page = p; load(); });
      $('#edit', root).onclick = () => resellerForm(r, load);
      const del = $('#del', root); if (del) del.onclick = async () => { if (await confirmBox('Delete reseller?', 'Their bot stops and the panel login is removed. Records are kept.', 'Delete')) { await api(`/resellers/${rid}`, { method: 'DELETE' }); location.hash = '#/reseller'; } };
      $('#settle', root).onclick = () => openModal({ title: 'Mark as settled', body: `<p class="muted">Resets the dues counter to zero (commission ${fmtMoney(due.commission)} received). If the store was paused it opens again.</p>${fld('Note', '<input name="note" maxlength="200">')}`, submit: 'Settle',
        onSubmit: async (v) => { await api(`/resellers/${rid}/settle`, { method: 'POST', body: v }); toast('Settled ✅'); load(); } });
    } catch (e) { root.innerHTML = empty('💥', e.message); }
  }
  load();
}
