// Resellers (admin): totals (today / yesterday / all / custom) with drill-down, list, add reseller.
import { $, api, avatar, debounce, drill, empty, esc, fld, fmtDate, fmtMoney, fmtNum, openModal, pager, rangeBlocks, rangeLabel, skeleton, tileHtml, toast } from '../core.js';

export function resellerForm(r = {}, done) {
  const edit = !!r.id;
  return openModal({ title: edit ? `Edit ${r.name}` : 'Add reseller', wide: true, submit: edit ? 'Save' : 'Create reseller',
    body: `<div class="two">${fld('Name', `<input name="name" required maxlength="60" value="${esc(r.name || '')}">`)}${fld('Telegram @username', `<input name="username" maxlength="40" value="${esc(r.username || '')}">`)}</div>
      <div class="two">${fld('Chat ID', `<input name="chat_id" required inputmode="numeric" value="${esc(r.chat_id || '')}">`)}${fld('Profile image', '<input type="file" name="avatar" accept="image/*">')}</div>
      <div class="two">${fld('Bot token', `<input name="bot_token" ${edit ? '' : 'required'} autocomplete="off" placeholder="${edit ? esc(r.bot_token) + ' (leave blank to keep)' : '123456:ABC…'}">`, 'From @BotFather. We verify it with Telegram.')}${fld('Bot name', `<input name="bot_name" maxlength="60" value="${esc(r.bot_name || '')}">`)}</div>
      <div class="two">${fld('Admin commission %', `<input name="commission_pct" type="number" step="0.1" min="0" max="100" value="${r.commission_pct ?? 10}" required>`)}${fld('Force maintenance at earnings ₹ (0 = never)', `<input name="due_limit" type="number" min="0" value="${r.due_limit || 0}">`, 'When their earnings reach this amount before settlement, their bot is paused until you settle.')}</div>
      <div class="two">${fld('Panel login username', `<input name="login_username" required maxlength="32" autocomplete="off" value="${esc(r.login_username || '')}">`)}${fld(edit ? 'New password (optional)' : 'Panel password', `<input name="password" type="password" autocomplete="new-password" ${edit ? '' : 'required'} minlength="10">`)}</div>
      ${fld('Note', `<input name="note" maxlength="400" value="${esc(r.note || '')}">`)}
      ${edit ? `<label class="row"><span class="switch"><input type="checkbox" name="active" value="1" ${r.active ? 'checked' : ''}><i></i></span> Active</label>` : ''}`,
    onSubmit: async (_d, form) => { const fd = new FormData(form); if (edit && !fd.has('active')) fd.set('active', '0'); await api(edit ? `/resellers/${r.id}` : '/resellers', { method: 'POST', form: fd }); toast(edit ? 'Saved ✨' : 'Reseller created 🎉'); done(); } });
}

export function drillResellerSales(range, rng) {
  drill({ title: `🏪 Reseller sales · ${rangeLabel(range)}`, header: (d) => `<div class="kpi-row">${tileHtml('Earned', fmtMoney(d.amount_total), `${fmtNum(d.count)} sales`, { big: true })}${tileHtml('Your commission', fmtMoney(d.commission_total), 'due from resellers', { hot: true })}</div>`,
    load: (p) => api('/resellers/earnings', { params: { range, ...rng, page: p } }),
    row: (e) => `<div class="li wide">${avatar(e.user.profile_image_url, e.user.full_name, 'lg')}<div class="meta"><b>${esc(e.user.full_name || '—')}</b><small>via <b>${esc(e.reseller_name)}</b> · ${esc(e.app_name)} ${esc(e.label)} · ${fmtDate(e.ts)}</small></div>
      <div class="acts"><b>${fmtMoney(e.amount)}</b><span class="badge warn">commission ${fmtMoney(e.commission)}</span></div></div>` });
}

export default async function (root) {
  let page = 1, q = '';
  root.innerHTML = `<div class="page-head"><div><h1>🏪 Reseller</h1><p>Each reseller runs their own bot, UPI and prices. You earn a commission on every sale.</p></div><button class="btn primary w-only" id="add">＋ Add reseller</button></div>
    <div id="blocks"></div>
    <div class="section-title"><h2>All resellers</h2><label class="search"><span>🔎</span><input id="q" type="search" placeholder="Name, @username, chat id" maxlength="64"></label></div>
    <div id="list" class="list">${skeleton(3)}</div><div id="pg"></div>`;
  async function load() {
    try {
      const d = await api('/resellers', { params: { q, page } });
      $('#list', root).innerHTML = d.items.length ? d.items.map((r) => `<div class="li wide ${r.due.forced ? 'forced' : ''}" data-id="${esc(r.id)}" tabindex="0">${avatar(r.avatar_url, r.name, 'lg')}
        <div class="meta"><b>${esc(r.name)} ${r.active ? '' : '<span class="badge bad">disabled</span>'} ${r.due.forced ? '<span class="badge bad">⛔ paused — dues</span>' : ''}</b>
        <small>${r.username ? '@' + esc(r.username) + ' · ' : ''}<span class="mono">${esc(r.chat_id)}</span> · 🤖 ${r.bot_username ? '@' + esc(r.bot_username) : '—'}</small>
        <div class="chips"><span class="badge violet">${r.commission_pct}% commission</span><span class="badge">due ${fmtMoney(r.due.commission)}</span><span class="badge">earned since settle ${fmtMoney(r.due.earned)}</span></div></div>
        <div class="acts"><b>${fmtMoney(r.total_earned)}</b><small class="muted">lifetime</small></div></div>`).join('') : empty('🏪', 'No resellers yet.');
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('click', (e) => { const r = e.target.closest('[data-id]'); if (r) location.hash = '#/reseller/' + r.dataset.id; });
  $('#add', root).onclick = () => resellerForm({}, load);
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); page = 1; load(); }));
  await rangeBlocks($('#blocks', root), {
    fetch: (start, end) => api('/resellers/overview', { params: { start, end } }),
    build: (d) => [{ title: '💸 Reseller earnings', key: 'earn', data: Object.fromEntries(Object.entries(d.totals).map(([k, v]) => [k, v.earned])), fmt: fmtMoney, sub: Object.fromEntries(Object.entries(d.totals).map(([k, v]) => [k, `${fmtNum(v.sales)} sales · ${v.resellers} resellers`])) },
      { title: '🏦 Your commission', key: 'comm', data: Object.fromEntries(Object.entries(d.totals).map(([k, v]) => [k, v.commission])), fmt: fmtMoney }],
    onDrill: (_k, range, rng) => drillResellerSales(range, rng),
  });
  load();
}
