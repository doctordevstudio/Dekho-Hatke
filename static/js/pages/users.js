import { $, api, debounce, drill, empty, esc, fmtDate, pager, skeleton, toast, fmtNum, confirmBox } from '../core.js';
import { bindUserRows, userRow } from './userui.js';

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>👥 Users</h1><p>Everyone who started the bot or got a manual key — tap a user for full details, purchase history and editing.</p></div>
    <div class="row"><label class="search grow"><span>🔎</span><input id="q" type="search" placeholder="Search name, @username, chat id or license key" maxlength="64"></label><button class="btn" id="bl">🚫 Blocked users</button></div></div>
    <div id="list" class="list">${skeleton(5)}</div><div id="pg"></div>`;
  let page = 1, users = [];
  async function load() {
    try {
      const d = await api('/users', { params: { q: $('#q', root).value.trim(), page } });
      users = d.items;
      $('#list', root).innerHTML = users.length ? users.map(userRow).join('') : empty('👻', 'No users found.');
      bindUserRows($('#list', root), users, load);
      pager($('#pg', root), d, (p) => { page = p; load(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
    } catch (e) { toast(e.message, 'err'); }
  }
  $('#q', root).addEventListener('input', debounce(() => { page = 1; load(); }));
  $('#bl', root).onclick = () => {
    const m = drill({ title: '🚫 Blocked users', load: (p) => api('/banned', { params: { page: p } }),
      row: (b, i) => `<div class="li wide"><div class="avatar">🚫</div><div class="meta"><b>${esc(b.name || 'User')} · <span class="mono">${esc(b.chat_id)}</span></b><small>${esc(b.reason)} · by ${esc(b.by)} · ${fmtDate(b.ts)}</small></div>
        <div class="acts"><button class="btn sm w-only" data-unban="${esc(b.chat_id)}">Unblock</button></div></div>` });
    m.el.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-unban]'); if (!b) return;
      if (await confirmBox('Unblock user?', 'The bot will answer them again.', 'Unblock', false)) { await api(`/users/${b.dataset.unban}/unban`, { method: 'POST' }); toast('Unblocked ✅'); m.close(); load(); }
    });
  };
  load();
}
