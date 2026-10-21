import { $, api, debounce, empty, pager, skeleton, toast } from './core.js';
import { bindUserList, userItem } from './licenseui.js';

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>👥 Users</h1><p>Everyone who started the bot or got a manual key — with their licenses.</p></div>
    <label class="search grow"><span>🔎</span><input id="q" type="search" placeholder="Search name, @username, chat id or license key" maxlength="64"></label></div>
    <div id="list" class="list">${skeleton(5)}</div><div id="pg"></div>`;
  let page = 1, users = [];
  async function load() {
    try {
      const d = await api('/users', { params: { q: $('#q', root).value.trim(), page } });
      users = d.items;
      $('#list', root).innerHTML = users.length ? users.map(userItem).join('') : empty('👻', 'No users found.');
      bindUserList($('#list', root), users, load);
      pager($('#pg', root), d, (p) => { page = p; load(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
    } catch (e) { toast(e.message, 'err'); }
  }
  $('#q', root).addEventListener('input', debounce(() => { page = 1; load(); }));
  load();
}
