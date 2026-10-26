// Plans: pick an app → see its plans. Sold counts are split between the admin bot and the reseller bots.
import { $, api, avatar, confirmBox, debounce, drill, empty, esc, fld, fmtDate, fmtMoney, fmtNum, openModal, skeleton, tileHtml, toast, userCell } from '../core.js';

const split = (main, res) => `<span class="badge info" title="Admin bot">🤖 ${fmtNum(main)}</span> <span class="badge warn" title="Reseller bots">🏪 ${fmtNum(res)}</span>`;

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>🏷️ Plans</h1><p>Choose an app to see its plans. 🤖 = sold by the admin bot · 🏪 = sold by reseller bots.</p></div><button class="btn primary w-only" id="add">＋ Add plan</button></div>
    <div id="tiles" class="grid auto">${skeleton(1)}</div><div id="body"></div>`;
  let d, apps = [], upis = [], shorts = [], sel = '', q = '';

  async function load() {
    [d, { apps }, { items: upis }, { items: shorts }] = await Promise.all([api('/plans'), api('/apps'), api('/upis'), api('/shorteners/options').catch(() => ({ items: [] }))]);
    const t = d.totals;
    $('#tiles', root).innerHTML = tileHtml('🤖 Admin bot sales', fmtNum(t.main), fmtMoney(t.amount_main), { drill: 'scope:main' })
      + tileHtml('🏪 Reseller bot sales', fmtNum(t.reseller), fmtMoney(t.amount_reseller), { drill: 'scope:reseller' })
      + tileHtml('Total plans sold', fmtNum(t.main + t.reseller), fmtMoney(t.amount_main + t.amount_reseller), { drill: 'scope:all' });
    paint();
  }
  const paint = () => (sel ? paintApp() : paintApps());

  function paintApps() {
    const rows = d.apps.filter((a) => !q || a.app_name.toLowerCase().includes(q));
    $('#body', root).innerHTML = `<div class="row gap-top"><input id="q" class="grow" type="search" placeholder="🔎 Search apps…" value="${esc(q)}"></div>
      <div class="grid auto gap-top">${rows.map((a) => `<button type="button" class="card app-pick" data-app="${esc(a.app_id)}"><div class="row">${avatar(a.image_url, a.app_name, 'lg')}<div class="grow left"><b>${esc(a.app_name)}</b><br><small class="muted">${fmtNum(a.plans)} plan${a.plans === 1 ? '' : 's'} · ${fmtNum(a.sold)} sold</small></div><span class="muted">›</span></div>
        <div class="row gap-top">${split(a.sold_main, a.sold_reseller)} <small class="muted">${fmtMoney(a.amount_main + a.amount_reseller)}</small></div></button>`).join('') || empty('📱', 'No apps found.')}</div>`;
    const qi = $('#q', root);
    qi.oninput = debounce(() => { q = qi.value.trim().toLowerCase(); const pos = qi.selectionStart; paintApps(); const n = $('#q', root); n.focus(); n.setSelectionRange(pos, pos); }, 200);
  }

  function paintApp() {
    const a = d.apps.find((x) => x.app_id === sel);
    if (!a) { sel = ''; return paintApps(); }
    const list = d.items.filter((p) => p.app_id === sel);
    $('#body', root).innerHTML = `<div class="row between gap-top"><button class="btn" id="back">← All apps</button><button class="btn sm" data-sales="">💸 All sales of this app</button></div>
      <div class="section-title"><h2>📱 ${esc(a.app_name)}</h2><div>${split(a.sold_main, a.sold_reseller)}</div></div>
      <div class="list">${list.map((p) => `<div class="li wide ${p.disabled ? 'dim' : ''}"><div class="avatar">${p.kind === 'trial' ? '🎁' : p.kind === 'short' ? '🔗' : '🏷️'}</div>
        <div class="meta"><b><span class="badge violet" title="Sort number">#${p.sort || '–'}</span> ${p.kind === 'trial' ? 'FREE TRIAL' : p.kind === 'short' ? '🆓 FREE via short link' : fmtMoney(p.amount)} · ${esc(p.label)} ${p.is_best ? '<span class="badge ok">⭐ best value</span>' : ''}${p.disabled ? ' <span class="badge bad">hidden in bot</span>' : ''}</b><small>${split(p.sold_main, p.sold_reseller)} · ${fmtNum(p.sold)} sold${p.kind === 'trial' ? '' : p.kind === 'short' ? ` · 🔗 ${esc(p.shorturl_name || '(removed)')}${p.shorturl_ok ? '' : ' <span class="badge bad">shortener off / removed — hidden in bot</span>'}` : ` · 🏦 ${p.upi_id ? esc(p.upi_label) : 'random UPI'}`}</small></div>
        <div class="acts"><label class="row tiny muted w-only" title="Show this plan in the bot"><span class="switch"><input type="checkbox" data-pdis="${esc(p.id)}" ${p.disabled ? '' : 'checked'}><i></i></span> In bot</label><button class="btn sm" data-sales="${esc(p.id)}">View sales</button><button class="btn sm w-only" data-edit="${esc(p.id)}">✏️ Edit</button><button class="btn sm danger w-only del-only" data-del="${esc(p.id)}">🗑</button></div></div>`).join('') || empty('🏷️', 'No plans for this app yet.')}</div>`;
  }

  // sales dialog; the scope buttons inside it re-open it with another filter (app / plan are carried in data attributes)
  function sales(scope, appId = '', planId = '', title = '💸 Plans sold') {
    const m = drill({ title, load: (page) => api('/plans/sales', { params: { app_id: appId, plan_id: planId, scope, page } }),
      header: (r) => `<div class="seller-note"><b>${fmtNum(r.count)}</b> sales · <b>${fmtMoney(r.amount_total)}</b></div>
        <div class="row gap-top">${[['all', 'All'], ['main', '🤖 Admin bot'], ['reseller', '🏪 Reseller bots']].map(([k, l]) => `<button type="button" class="btn sm ${k === scope ? 'primary' : ''}" data-scope="${k}">${l}</button>`).join('')}</div>`,
      row: (s) => `<div class="li">${userCell(s.user)}<div class="meta"><b>${fmtMoney(s.amount)} · ${esc(s.app_name)} — ${esc(s.plan_label)}</b><small>${s.reseller ? '🏪' : '🤖'} ${esc(s.bot)} · ${fmtDate(s.ts)}</small></div></div>` });
    m.el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-scope]'); if (!b || b.dataset.scope === scope) return;
      m.close(); sales(b.dataset.scope, appId, planId, title);
    });
  }

  const shortOpts = (p) => {
    const list = shorts.slice();
    if (p.shorturl_id && !list.some((x) => x.id === p.shorturl_id)) list.push({ id: p.shorturl_id, name: (p.shorturl_name || 'removed shortener') + ' (off)', pages: 0 });
    return list.map((x) => `<option value="${esc(x.id)}" ${x.id === p.shorturl_id ? 'selected' : ''}>🔗 ${esc(x.name)}${x.pages ? ` · ${x.pages} page${x.pages === 1 ? '' : 's'}` : ''}</option>`).join('');
  };
  const nextSort = (appId) => 1 + Math.max(0, ...d.items.filter((x) => x.app_id === appId).map((x) => x.sort || 0));
  function form(p = {}) {
    const edit = !!p.id;
    const m = openModal({ title: edit ? 'Edit plan' : 'Add plan', body: `${edit ? '' : fld('App', `<select name="app_id">${apps.map((a) => `<option value="${esc(a.id)}" ${a.id === sel ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select>`)}
      ${fld('Type', `<select name="kind"><option value="paid" ${p.kind !== 'trial' ? 'selected' : ''}>Paid plan</option><option value="trial" ${p.kind === 'trial' ? 'selected' : ''}>🎁 Free trial</option></select>`)}
      <div id="shBox">${fld('Free via short link', `<select name="shorturl_id"><option value="">— None (normal paid plan) —</option>${shortOpts(p)}</select>`, 'Optional. Pick a shorturl from the Shortener page (only ones that generate license keys). The bot shows this plan as FREE: the user completes the pages and then receives the key.')}</div>
      <div class="two">${fld('Sort number *', `<input name="sort" type="number" min="1" max="999" step="1" required placeholder="1 = shown first" value="${p.sort || nextSort(p.app_id || sel || (apps[0] || {}).id)}">`, 'Plans are shown to users in this order (1 first).')}
        <div id="upiBox">${fld('UPI ID for this plan *', `<select name="upi_id"><option value="" ${!p.upi_id ? 'selected' : ''}>🎲 Random UPI (round-robin)</option>${upis.filter((u) => u.active !== false).map((u) => `<option value="${esc(u.id)}" ${u.id === p.upi_id ? 'selected' : ''}>${esc(u.name)} · ${esc(u.upi_id)}</option>`).join('')}</select>`, 'Payments for this plan go to this UPI, or rotate across all active UPIs.')}</div></div>
      <div id="amt">${fld('Price ₹ *', `<input name="amount" type="number" min="1" step="0.01" placeholder="e.g. 299" value="${p.amount || ''}">`)}</div>
      <div class="two">${fld('Validity *', `<input name="validity_value" type="number" min="1" required placeholder="e.g. 30" value="${p.validity_value || ''}">`)}${fld('Unit *', `<select name="validity_unit">${['hours', 'days', 'years'].map((u) => `<option ${u === (p.validity_unit || 'days') ? 'selected' : ''}>${u}</option>`).join('')}</select>`)}</div>
      <label class="row"><span class="switch"><input type="checkbox" name="is_best" ${p.is_best ? 'checked' : ''}><i></i></span> ⭐ Highlight as best value</label>`,
      onSubmit: async (v, f) => { await api(edit ? `/plans/${p.id}` : '/plans', { method: edit ? 'PATCH' : 'POST', body: { ...v, is_best: f.elements.is_best.checked } }); toast('Saved ✨'); load(); } });
    const sync = () => {
      const t = m.form.elements.kind.value === 'trial', s = !t && !!m.form.elements.shorturl_id.value;
      $('#amt', m.el).hidden = t || s; $('#upiBox', m.el).hidden = t || s; $('#shBox', m.el).hidden = t;
      m.form.elements.is_best.closest('label').hidden = t || s;
    };
    m.form.elements.kind.addEventListener('change', sync); m.form.elements.shorturl_id.addEventListener('change', sync); sync();
  }

  root.addEventListener('change', async (e) => {
    const t = e.target.closest('[data-pdis]'); if (!t) return;
    try { await api(`/plans/${t.dataset.pdis}/disable`, { method: 'POST', body: { disabled: !t.checked } }); toast(t.checked ? 'Plan shown in the bot ✅' : 'Plan hidden from the bot 🙈'); load(); }
    catch (er) { toast(er.message, 'err'); t.checked = !t.checked; }
  });
  root.addEventListener('click', async (e) => {
    const pick = e.target.closest('[data-app]'); if (pick) { sel = pick.dataset.app; return paint(); }
    if (e.target.closest('#back')) { sel = ''; return paint(); }
    const ed = e.target.closest('[data-edit]'), del = e.target.closest('[data-del]'), sl = e.target.closest('[data-sales]'), tile = e.target.closest('[data-drill]');
    if (ed) return form(d.items.find((p) => p.id === ed.dataset.edit));
    if (del) { if (await confirmBox('Delete plan?', 'Users can no longer buy it. Past purchases are unaffected.', 'Delete')) { await api(`/plans/${del.dataset.del}`, { method: 'DELETE' }); load(); } return; }
    if (sl) return sales('all', sel, sl.dataset.sales, '💸 Sales');
    if (tile && tile.dataset.drill.startsWith('scope:')) sales(tile.dataset.drill.slice(6), '', '', '💸 Plans sold');
  });
  $('#add', root).onclick = () => (apps.length ? form() : toast('Create an app first', 'err'));
  load();
}
