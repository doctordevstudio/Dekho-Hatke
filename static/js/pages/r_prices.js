// Reseller prices: the admin sets the base price; a reseller can only INCREASE it (and hide plans from their own bot).
import { $, api, avatar, empty, esc, fmtMoney, pager, skeleton, toast, debounce } from '../core.js';

export default async function (root) {
  let page = 1, q = '';
  root.innerHTML = `<div class="page-head"><div><h1>🏷️ Prices</h1><p>You can only <b>increase</b> prices. The base price (set by the admin) is the minimum. Changes apply to your bot only.</p></div>
    <label class="search"><span>🔎</span><input id="q" type="search" placeholder="Filter apps" maxlength="40"></label></div><div id="list">${skeleton(3)}</div><div id="pg"></div>`;
  async function load() {
    try {
      const d = await api('/prices', { params: { page } });
      const rows = d.items.filter((a) => !q || a.app_name.toLowerCase().includes(q.toLowerCase()) || a.category.toLowerCase().includes(q.toLowerCase()));
      $('#list', root).innerHTML = rows.length ? rows.map((a) => `<div class="card gap-top"><div class="row">${avatar(a.image_url, a.app_name, 'lg')}<div><b>${esc(a.app_name)}</b><br><small class="muted">${esc(a.category)}</small></div></div>
        <div class="list gap-top">${a.plans.map((p) => `<form class="li wide" data-plan="${esc(p.id)}"><div class="meta"><b>${esc(p.label)} ${p.is_best ? '⭐' : ''}</b><small>base ${fmtMoney(p.base)}</small></div>
          <div class="acts"><input name="price" type="number" min="${p.base}" step="1" value="${p.price}" style="width:110px" aria-label="Your price"><label class="switch" title="Show in my bot"><input type="checkbox" name="enabled" ${p.enabled ? 'checked' : ''}><i></i></label><button class="btn sm primary">Save</button></div></form>`).join('')}</div></div>`).join('') : empty('🏷️', 'No apps available yet.');
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('submit', async (e) => { const f = e.target.closest('[data-plan]'); if (!f) return; e.preventDefault();
    try { await api(`/prices/${f.dataset.plan}`, { method: 'PUT', body: { price: f.elements.price.value, enabled: f.elements.enabled.checked } }); toast('Saved ✨'); } catch (er) { toast(er.message, 'err'); } });
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); load(); }));
  load();
}
