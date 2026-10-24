// One seller (admin): full details, earnings by range (paginated sales with buyer + amount), their apps, withdrawals, edit.
import { $, api, avatar, confirmBox, empty, esc, fmtDate, fmtMoney, fmtNum, pager, rangeBar, skeleton, tileHtml, toast } from '../core.js';
import { sellerForm } from './a_sellers.js';

export default async function (root, sid) {
  let rng = { range: 'today', start: '', end: '' }, page = 1;
  root.innerHTML = skeleton(3);
  async function load() {
    try {
      const d = await api(`/sellers/${sid}`, { params: { ...rng, page } });
      const s = d.seller;
      root.innerHTML = `<div class="page-head"><div class="row">${avatar(s.profile_image_url, s.name, 'lg')}<div><h1>${esc(s.name)} ${s.active ? '' : '<span class="badge bad">disabled</span>'}</h1><p>${s.username ? '@' + esc(s.username) + ' · ' : ''}<span class="mono">${esc(s.chat_id || 'no chat id')}</span> · login <b>${esc(s.login_username)}</b> · joined ${fmtDate(s.created_at)}</p></div></div>
        <div class="row"><a class="btn" href="#/seller">← Back</a><button class="btn w-only" id="edit">✏️ Edit seller</button><button class="btn danger w-only del-only" id="del">🗑</button></div></div>
        ${s.note ? `<div class="seller-note">📝 ${esc(s.note)}</div>` : ''}
        <div class="grid cols-4 keep2 gap-top">${tileHtml('Today', fmtMoney(d.ranges.today.earned), `${fmtNum(d.ranges.today.sales)} sales`, { hot: true })}${tileHtml('Yesterday', fmtMoney(d.ranges.yesterday.earned), `${fmtNum(d.ranges.yesterday.sales)} sales`)}
          ${tileHtml('All time', fmtMoney(d.ranges.all.earned), `${fmtNum(d.ranges.all.sales)} sales`)}${tileHtml('Wallet balance', fmtMoney(s.balance), `${s.apps_published} apps live`, { big: true })}</div>
        <div class="section-title"><h2>📱 Apps</h2></div>
        <div class="list">${d.apps.length ? d.apps.map((a) => `<div class="li wide">${avatar(a.image_url, a.name, 'lg')}<div class="meta"><b>${esc(a.name)} ${a.disabled ? '<span class="badge bad">disabled</span>' : ''}</b><small>${fmtNum(a.sales)} sales · earned ${fmtMoney(a.earned)}</small></div>
          <div class="acts"><span class="badge ${a.status === 'live' ? 'ok' : a.status === 'rejected' ? 'bad' : 'warn'}">${a.status.replace('_', ' ')}</span><a class="btn sm" href="#/apps/${esc(a.id)}">Open</a>
          <button class="btn sm w-only" data-dis="${esc(a.id)}" data-v="${a.disabled ? 0 : 1}">${a.disabled ? '▶️ Enable' : '⏸ Disable'}</button><button class="btn sm danger w-only del-only" data-del="${esc(a.id)}">🗑</button></div></div>`).join('') : empty('📱', 'No apps yet.')}</div>
        <div class="section-title"><h2>💰 Sales</h2><div id="rb"></div></div>
        <p class="tiny muted">${fmtNum(d.period_count)} sale${d.period_count === 1 ? '' : 's'} · ${fmtMoney(d.period_total)}</p>
        <div class="list">${d.items.length ? d.items.map((e) => `<div class="li wide"><div class="avatar">💵</div><div class="meta"><b>${esc(e.user.full_name || '—')}</b><small>bought ${esc(e.app_name)} · ${esc(e.plan_label)} · <span class="mono">${esc(e.user.chat_id)}</span> · ${fmtDate(e.ts)}</small></div><div class="acts"><b>${fmtMoney(e.amount)}</b><small class="muted">list ${fmtMoney(e.gross)}</small></div></div>`).join('') : empty('🪙', 'No sales in this period.')}</div><div id="pg"></div>
        <div class="section-title"><h2>🏧 Recent withdrawals</h2></div>
        <div class="list">${d.withdrawals.length ? d.withdrawals.map((w) => `<div class="li"><div class="avatar">💸</div><div class="meta"><b>${fmtMoney(w.amount)} → ${esc(w.upi_id)}</b><small>${fmtDate(w.created_at)}${w.reason ? ' · ' + esc(w.reason) : ''}</small></div><div class="acts"><span class="badge ${w.status === 'approved' ? 'ok' : w.status === 'rejected' ? 'bad' : 'warn'}">${w.status}</span></div></div>`).join('') : empty('🏧', 'None yet.')}</div>`;
      $('#rb', root).appendChild(rangeBar((x) => { rng = x; page = 1; load(); }, { initial: rng.range }).el);
      pager($('#pg', root), d, (p) => { page = p; load(); });
      $('#edit', root).onclick = () => sellerForm(s, load);
      const del = $('#del', root); if (del) del.onclick = async () => { if (await confirmBox('Delete seller?', 'Their apps stop selling, the login is removed. Records are kept.', 'Delete')) { await api(`/sellers/${sid}`, { method: 'DELETE' }); location.hash = '#/seller'; } };
      root.querySelectorAll('[data-dis]').forEach((b) => (b.onclick = async () => { await api(`/apps/${b.dataset.dis}/disable`, { method: 'POST', body: { disabled: b.dataset.v === '1' } }); toast('Updated'); load(); }));
      root.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => { if (await confirmBox('Delete app?', 'It disappears from the shop; the API stops working.', 'Delete')) { await api(`/apps/${b.dataset.del}`, { method: 'DELETE' }); load(); } }));
    } catch (e) { root.innerHTML = empty('💥', e.message); }
  }
  load();
}
