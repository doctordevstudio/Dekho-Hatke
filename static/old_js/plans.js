import { $, api, confirmBox, empty, esc, fmtMoney, fmtNum, openModal, skeleton, toast } from './core.js';

const validityFields = (p) => `<div class="two"><label class="field"><span>Amount (₹)</span><input type="number" name="amount" min="1" step="any" required value="${p ? esc(p.amount) : ''}"></label>
  <label class="field"><span>Validity</span><div class="two"><input type="number" name="validity_value" min="1" required value="${p ? esc(p.validity_value) : 30}">
  <select name="validity_unit"><option value="days" ${p?.validity_unit === 'days' || !p ? 'selected' : ''}>Days</option><option value="hours" ${p?.validity_unit === 'hours' ? 'selected' : ''}>Hours</option></select></div></label></div>`;

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>🏷️ Plans</h1><p>What your users can buy in the bot.</p></div><button class="btn primary w-only" id="add">＋ Add plan</button></div>
    <div id="sold" class="grid cols-4 keep2">${skeleton(1)}</div><div class="section-title"><h2>All plans</h2></div><div id="list" class="list"></div>`;
  let data;
  async function load() {
    try {
      data = await api('/plans');
      $('#sold', root).innerHTML = data.sold_by_app.length ? data.sold_by_app.map((a) => `<div class="tile hot"><div class="lbl">${esc(a.app_name)}</div><div class="val">${fmtNum(a.sold)}</div><div class="sub">plans sold</div></div>`).join('') : '';
      $('#list', root).innerHTML = data.items.length ? data.items.map((p) => `<div class="li"><div class="avatar">🏷️</div>
        <div class="meta"><b>${esc(p.app_name)} · ${esc(p.label)}</b><small>${fmtMoney(p.amount)} · <span class="mono">${esc(p.id)}</span></small></div>
        <div class="acts"><span class="badge ok">${fmtNum(p.sold)} sold</span><button class="btn sm w-only" data-edit="${esc(p.id)}">✏️ Edit</button><button class="btn sm danger w-only" data-del="${esc(p.id)}">🗑 Delete</button></div></div>`).join('')
        : empty('🧺', 'No plans yet. Add one so users can buy in the bot.');
    } catch (e) { toast(e.message, 'err'); }
  }
  await load();
  $('#add', root).addEventListener('click', async () => {
    const apps = (await api('/apps')).apps;
    if (!apps.length) return toast('Create an app first', 'err');
    openModal({ title: 'Create plan', submit: 'Create plan',
      body: `<label class="field"><span>App</span><select name="app_id" required>${apps.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}</select></label>${validityFields(null)}`,
      onSubmit: async (v) => { await api('/plans', { method: 'POST', body: v }); toast('Plan created'); load(); } });
  });
  $('#list', root).addEventListener('click', async (e) => {
    const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]');
    if (ed) {
      const p = data.items.find((x) => x.id === ed.dataset.edit);
      openModal({ title: `Edit plan · ${p.app_name}`, body: validityFields(p),
        onSubmit: async (v) => { await api('/plans/' + p.id, { method: 'PATCH', body: v }); toast('Plan updated'); load(); } });
    } else if (del && await confirmBox('Delete this plan?', 'It disappears from the bot. Past sales are kept.', 'Delete')) {
      await api('/plans/' + del.dataset.del, { method: 'DELETE' }); toast('Plan deleted'); load();
    }
  });
}
