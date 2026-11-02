// Daily Check-in and Spin Wheel: settings + stats (valid / expired / claimed / used for purchases) + every claim, paginated.
import { $, api, avatar, bindDrill, debounce, empty, esc, fld, fmtDate, fmtMoney, fmtNum, openModal, pager, rangeBar, rangeLabel, skeleton, tileHtml, toast, until } from '../core.js';
import { userDetail } from './userui.js';

const META = { checkin: ['📅', 'Daily Check-in', 'Users claim a random bonus once a day. It resets at 12 AM.'], spin: ['🎡', 'Spin Wheel', 'A spin animation, then “you won ₹X”. One spin per day, resets at 12 AM.'] };

export default async function (root, kind) {
  const [icon, title, blurb] = META[kind];
  let rng = { range: 'today', start: '', end: '' }, page = 1, q = '', filter = '', items = [];
  root.innerHTML = `<div class="page-head"><div><h1>${icon} ${title}</h1><p>${blurb} Bonus money expires after the time you set and can only be used on purchases.</p></div><div id="rb"></div></div>
    <div id="tiles" class="grid cols-4 keep2">${skeleton(1)}</div>
    <div class="card gap-top" id="cfg"></div>
    <div class="row between gap-top"><div class="seg" id="seg"></div><label class="search grow"><span>🔎</span><input id="q" type="search" placeholder="Search user" maxlength="64"></label></div>
    <div id="list" class="list gap-top"></div><div id="pg"></div>`;
  $('#rb', root).appendChild(rangeBar((r) => { rng = r; page = 1; load(); }).el);
  const SEG = [['', 'All'], ['valid', 'Valid'], ['expired', 'Expired'], ['used', 'Used']];
  const drawSeg = () => { $('#seg', root).innerHTML = SEG.map(([v, l]) => `<button data-v="${v}" class="${v === filter ? 'on' : ''}">${l}</button>`).join(''); };
  drawSeg();
  $('#seg', root).onclick = (e) => { const b = e.target.closest('[data-v]'); if (b) { filter = b.dataset.v; drawSeg(); page = 1; draw(); } };
  let last;
  function cfgForm(s) {
    $('#cfg', root).innerHTML = `<h3>⚙️ Settings</h3><form id="cf"><div class="grid cols-4">
      ${fld('Minimum ₹', `<input name="min" type="number" step="0.01" min="0" value="${s.min}" required>`)}${fld('Maximum ₹', `<input name="max" type="number" step="0.01" min="0.01" value="${s.max}" required>`)}
      ${fld('Expires after (minutes)', `<input name="expire_minutes" type="number" min="1" value="${s.expire_minutes}" required>`)}${fld('Reminder before expiry (min, 0 = none)', `<input name="notify_before" type="number" min="0" value="${s.notify_before}" required>`)}</div>
      <div class="row between gap-top wrap"><label class="row"><span class="switch"><input type="checkbox" name="enabled" ${s.enabled ? 'checked' : ''}><i></i></span> Enabled for users</label>
      <label class="row"><span class="switch"><input type="checkbox" name="decimals" ${s.decimals ? 'checked' : ''}><i></i></span> Allow decimals (up to 2 places, e.g. ₹2.35)</label><button class="btn primary w-only">Save</button></div></form>`;
    $('#cf', root).onsubmit = async (e) => { e.preventDefault(); const f = new FormData(e.target); const body = { enabled: f.get('enabled') === 'on', decimals: f.get('decimals') === 'on', min: f.get('min'), max: f.get('max'), expire_minutes: f.get('expire_minutes'), notify_before: f.get('notify_before') };
      try { await api(`/rewards/${kind}/settings`, { method: 'PUT', body }); toast('Saved ✨'); load(); } catch (er) { toast(er.message, 'err'); } };
  }
  function draw() {
    const rows = last.items.filter((r) => !filter || (filter === 'used' ? r.used > 0 : r.status === filter));
    items = rows;
    $('#list', root).innerHTML = rows.length ? rows.map((r, i) => `<div class="li wide" data-i="${i}" tabindex="0">${avatar(r.user.profile_image_url, r.user.full_name, 'lg')}
      <div class="meta"><b>${esc(r.user.full_name || '—')}</b><small>${r.user.username ? '@' + esc(r.user.username) + ' · ' : ''}<span class="mono">${esc(r.user.chat_id)}</span> · claimed ${fmtDate(r.ts)}</small>
      <div class="chips"><span class="badge ${{ valid: 'ok', expired: 'bad', used: 'info' }[r.status] || ''}">${r.status}${r.status === 'valid' ? ' · ' + until(r.expires_at) : ''}</span><span class="badge">used ${fmtMoney(r.used)}</span><span class="badge">left ${fmtMoney(r.remaining)}</span></div>
      ${r.uses.map((u) => `<small class="muted">${u.status === 'held' ? '⏳ held' : '✅ used'} ${fmtMoney(u.amount)} for ${esc(u.app_name)} ${esc(u.plan_label)} · ${fmtDate(u.ts)}</small>`).join('')}</div><div class="acts"><b>${fmtMoney(r.amount)}</b></div></div>`).join('') : empty(icon, 'No claims in this period.');
  }
  async function load() {
    try {
      last = await api(`/rewards/${kind}`, { params: { ...rng, q, page } });
      const s = last.stats;
      $('#tiles', root).innerHTML = tileHtml('Claims', fmtNum(s.claims), `${fmtNum(s.claimed_users)} users · ${rangeLabel(rng.range)}`, { big: true }) + tileHtml('Claimed', fmtMoney(s.claimed_amount), 'total given', { hot: true }) +
        tileHtml('Valid now', fmtMoney(s.valid_amount), 'still usable') + tileHtml('Expired', fmtMoney(s.expired_amount), 'lost by users') +
        tileHtml('Used on purchases', fmtMoney(s.used_amount), `${fmtNum(s.used_users)} users`);
      if (!$('#cf', root)) cfgForm(last.settings);
      draw();
      pager($('#pg', root), last, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('click', (e) => { const r = e.target.closest('[data-i]'); if (r) userDetail(items[+r.dataset.i].user.chat_id); });
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); page = 1; load(); }));
  load();
}
