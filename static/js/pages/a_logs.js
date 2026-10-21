import { $, api, debounce, empty, esc, fmtDate, pager, skeleton, tabsBar, toast } from '../core.js';

export default async function (root) {
  let section = '', page = 1, q = '', sections = [];
  root.innerHTML = `<div class="page-head"><div><h1>📜 Logs</h1><p>Everything that happens, split by section. (IP addresses are stored for safety but never shown.)</p></div>
    <label class="search grow"><span>🔎</span><input id="q" type="search" placeholder="Search action, details, target or who" maxlength="64"></label></div>
    <div id="tb" class="tabs wrap"></div><div id="list" class="list gap-top">${skeleton(5)}</div><div id="pg"></div>`;
  let tb;
  async function load() {
    try {
      const d = await api('/logs', { params: { section, q, page } });
      if (!tb) { sections = [['', 'All'], ...d.sections.map((s) => [s.key, s.label])]; tb = tabsBar(sections, section, (k) => { section = k; page = 1; load(); }); tb.el.classList.add('wrap'); $('#tb', root).replaceWith(tb.el); }
      $('#list', root).innerHTML = d.items.length ? d.items.map((r) => `<div class="li wide"><div class="avatar">${r.source === 'bot' ? '🤖' : r.source === 'seller' ? '🚀' : r.source === 'reseller' ? '🏪' : '🧑‍💻'}</div>
        <div class="meta"><b>${esc(r.action)}</b><small>${esc(r.details)}${r.target ? ` · <span class="mono">${esc(r.target)}</span>` : ''}</small></div>
        <div class="acts"><span class="badge">${esc(r.admin || 'system')}</span><small class="muted">${fmtDate(r.ts)}</small></div></div>`).join('') : empty('📜', 'No log entries.');
      pager($('#pg', root), d, (p) => { page = p; load(); window.scrollTo({ top: 0 }); });
    } catch (e) { toast(e.message, 'err'); }
  }
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); page = 1; load(); }));
  load();
}
