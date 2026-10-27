import { $, api, debounce, empty, esc, fmtDate, pager, skeleton, toast } from './core.js';

const ICON = { auth: '🔐', app: '📱', license: '🔑', txn: '🧾', upi: '🏦', plan: '🏷️', settings: '⚙️', maintenance: '🛠', channel: '📢', admin: '🛡' };

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>📜 Logs</h1><p>Every change, and who made it. IP addresses are recorded privately and never shown.</p></div></div>
    <div class="row"><label class="search grow"><span>🔎</span><input id="q" type="search" placeholder="Search action, target or details" maxlength="60"></label>
    <label class="search"><span>👤</span><input id="who" type="search" placeholder="Filter by admin" maxlength="40"></label></div>
    <div id="list" class="list">${skeleton(5)}</div><div id="pg"></div>`;
  let page = 1;
  async function load() {
    try {
      const d = await api('/logs', { params: { q: $('#q', root).value.trim(), admin: $('#who', root).value.trim(), page } });
      $('#list', root).innerHTML = d.items.length ? d.items.map((l) => `<div class="li"><div class="avatar">${ICON[l.action.split('.')[0]] || '•'}</div>
        <div class="meta"><b>${esc(l.action)}${l.target ? ` <span class="muted mono">· ${esc(l.target)}</span>` : ''}</b><small>${esc(l.details)}</small></div>
        <div class="acts"><span class="badge ${l.source === 'telegram' ? 'info' : l.source === 'system' ? '' : 'violet'}">${l.source === 'telegram' ? '✈️' : l.source === 'system' ? '🤖' : '🖥'} ${esc(l.admin)}</span><small class="muted">${fmtDate(l.ts)}</small></div></div>`).join('')
        : empty('📭', 'No log entries match.');
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  const reload = debounce(() => { page = 1; load(); });
  $('#q', root).addEventListener('input', reload); $('#who', root).addEventListener('input', reload);
  load();
}
