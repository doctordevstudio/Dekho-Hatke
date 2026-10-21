// Plans: paid plans (hours / days / years) + one free-trial plan per app; mark one "best value"; sold counts.
import { $, api, confirmBox, empty, esc, fld, fmtMoney, fmtNum, openModal, skeleton, tileHtml, toast } from '../core.js';
import { drillSales } from './a_home.js';

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>🏷️ Plans</h1><p>Validity can be hours, days or years. A free trial can be claimed once per user per app.</p></div><button class="btn primary w-only" id="add">＋ Add plan</button></div>
    <div id="sold" class="grid auto">${skeleton(1)}</div><div id="body"></div>`;
  let d, apps = [];
  async function load() {
    [d, { apps }] = await Promise.all([api('/plans'), api('/apps')]);
    $('#sold', root).innerHTML = d.sold_by_app.map((a) => tileHtml(esc(a.app_name), fmtNum(a.sold), 'plans sold', { drill: a.app_id })).join('') || '';
    const by = {};
    d.items.forEach((p) => (by[p.app_id] = by[p.app_id] || []).push(p));
    $('#body', root).innerHTML = Object.entries(by).map(([id, list]) => `<div class="section-title"><h2>📱 ${esc(list[0].app_name)}</h2></div><div class="list">${list.map((p) => `<div class="li wide">
      <div class="avatar">${p.kind === 'trial' ? '🎁' : '🏷️'}</div><div class="meta"><b>${p.kind === 'trial' ? 'FREE TRIAL' : fmtMoney(p.amount)} · ${esc(p.label)} ${p.is_best ? '<span class="badge ok">⭐ best value</span>' : ''}</b><small>${fmtNum(p.sold)} sold</small></div>
      <div class="acts"><button class="btn sm w-only" data-edit="${esc(p.id)}">✏️ Edit</button><button class="btn sm danger w-only del-only" data-del="${esc(p.id)}">🗑</button></div></div>`).join('')}</div>`).join('') || empty('🏷️', 'No plans yet. Create an app in Purchase first, then add plans here.');
  }
  function form(p = {}) {
    const edit = !!p.id;
    const m = openModal({ title: edit ? 'Edit plan' : 'Add plan', body: `${edit ? '' : fld('App', `<select name="app_id">${apps.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}</select>`)}
      ${fld('Type', `<select name="kind"><option value="paid" ${p.kind !== 'trial' ? 'selected' : ''}>Paid plan</option><option value="trial" ${p.kind === 'trial' ? 'selected' : ''}>🎁 Free trial</option></select>`)}
      <div id="amt">${fld('Price ₹', `<input name="amount" type="number" min="1" step="0.01" value="${p.amount || ''}">`)}</div>
      <div class="two">${fld('Validity', `<input name="validity_value" type="number" min="1" required value="${p.validity_value || 1}">`)}${fld('Unit', `<select name="validity_unit">${['hours', 'days', 'years'].map((u) => `<option ${u === (p.validity_unit || 'days') ? 'selected' : ''}>${u}</option>`).join('')}</select>`)}</div>
      <label class="row"><span class="switch"><input type="checkbox" name="is_best" ${p.is_best ? 'checked' : ''}><i></i></span> ⭐ Highlight as best value</label>`,
      onSubmit: async (v, form) => { const body = { ...v, is_best: form.elements.is_best.checked };
        await api(edit ? `/plans/${p.id}` : '/plans', { method: edit ? 'PATCH' : 'POST', body }); toast('Saved ✨'); load(); } });
    const sync = () => { $('#amt', m.el).hidden = m.form.elements.kind.value === 'trial'; };
    m.form.elements.kind.addEventListener('change', sync); sync();
  }
  root.addEventListener('click', async (e) => {
    const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), dr = e.target.closest('[data-drill]');
    if (ed) form(d.items.find((p) => p.id === ed.dataset.edit));
    if (del && await confirmBox('Delete plan?', 'Users can no longer buy it. Past purchases are unaffected.', 'Delete')) { await api(`/plans/${del.dataset.del}`, { method: 'DELETE' }); load(); }
    if (dr) drillSales('all', {}, dr.dataset.drill, '💸 Sales of this app');
  });
  $('#add', root).onclick = () => (apps.length ? form() : toast('Create an app first', 'err'));
  load();
}
