// Reseller prices & visibility: raise prices up to +100% of the base, and switch categories, apps, plans and free trials on/off in your own bot.
import { $, api, avatar, debounce, empty, esc, fmtMoney, pager, skeleton, toast } from '../core.js';

const sw = (attrs, on, title = '') => `<label class="switch" title="${esc(title)}"><input type="checkbox" ${attrs} ${on ? 'checked' : ''}><i></i></label>`;

export default async function (root) {
  let page = 1, q = '';
  root.innerHTML = `<div class="page-head"><div><h1>🏷️ Prices</h1><p>You can <b>increase</b> a price up to <b>100% above</b> the base price (2× the base). Use the switches to show or hide categories, apps, plans and free trials in your bot.</p></div>
    <label class="search"><span>🔎</span><input id="q" type="search" placeholder="Filter apps" maxlength="40"></label></div>
    <div class="card" id="cats"></div><div id="list">${skeleton(3)}</div><div id="pg"></div>`;

  async function load() {
    try {
      const d = await api('/prices', { params: { page } });
      $('#cats', root).innerHTML = `<h3>📁 Categories in my bot</h3><div class="chips gap-top">${d.categories.map((c) => `<label class="chip catsw">${esc(c.icon)} ${esc(c.name)} ${sw(`data-cat="${esc(c.id)}"`, !c.hidden, 'Show this category')}</label>`).join('') || '<span class="muted tiny">No categories.</span>'}</div>`;
      const rows = d.items.filter((a) => !q || a.app_name.toLowerCase().includes(q.toLowerCase()) || a.category.toLowerCase().includes(q.toLowerCase()));
      $('#list', root).innerHTML = rows.length ? rows.map((a) => `<div class="card gap-top ${a.hidden ? 'dim' : ''}"><div class="row between"><div class="row">${avatar(a.image_url, a.app_name, 'lg')}<div><b>${esc(a.app_name)}</b><br><small class="muted">${esc(a.category)}</small></div></div>
          <div class="row"><label class="row tiny muted">Show app ${sw(`data-app="${esc(a.app_id)}"`, !a.hidden, 'Show this app in my bot')}</label>${a.has_trial ? `<label class="row tiny muted">🎁 Free trial ${sw(`data-trial="${esc(a.app_id)}"`, a.trial, 'Offer the free trial in my bot')}</label>` : ''}</div></div>
        <div class="list gap-top">${a.plans.map((p) => `<form class="li wide" data-plan="${esc(p.id)}"><div class="meta"><b>${esc(p.label)} ${p.is_best ? '⭐' : ''}</b><small>base ${fmtMoney(p.base)} · max ${fmtMoney(p.max)} (+100%)</small></div>
          <div class="acts"><input name="price" type="number" min="${p.base}" max="${p.max}" step="0.01" value="${p.price}" style="width:110px" aria-label="Your price"><label class="switch" title="Show this plan in my bot"><input type="checkbox" name="enabled" ${p.enabled ? 'checked' : ''}><i></i></label><button class="btn sm primary">Save</button></div></form>`).join('')}</div></div>`).join('') : empty('🏷️', 'No apps available yet.');
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { toast(e.message, 'err'); }
  }

  const put = async (path, body, msg) => { try { await api(path, { method: 'PUT', body }); toast(msg); } catch (er) { toast(er.message, 'err'); load(); } };
  root.addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.cat) return put('/visibility', { kind: 'category', id: t.dataset.cat, enabled: t.checked }, t.checked ? 'Category shown' : 'Category hidden');
    if (t.dataset.app) return put('/visibility', { kind: 'app', id: t.dataset.app, enabled: t.checked }, t.checked ? 'App shown' : 'App hidden').then(load);
    if (t.dataset.trial) return put(`/trials/${t.dataset.trial}`, { enabled: t.checked }, t.checked ? 'Free trial ON' : 'Free trial OFF');
  });
  root.addEventListener('submit', async (e) => {
    const f = e.target.closest('[data-plan]'); if (!f) return; e.preventDefault();
    try { await api(`/prices/${f.dataset.plan}`, { method: 'PUT', body: { price: f.elements.price.value, enabled: f.elements.enabled.checked } }); toast('Saved ✨'); }
    catch (er) { toast(er.message, 'err'); }
  });
  $('#q', root).addEventListener('input', debounce(() => { q = $('#q', root).value.trim(); load(); }));
  load();
}
