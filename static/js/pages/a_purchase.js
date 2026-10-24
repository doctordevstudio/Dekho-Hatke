// Purchase: Categories → Apps → (video, features, plans). Category ranking by drag & drop; apps inside are ordered by most purchased.
import { $, $$, api, avatar, canSeeIds, confirmBox, empty, esc, fld, fmtNum, openModal, skeleton, toast } from '../core.js';

export function appForm({ app = {}, cats, onDone }) {
  const edit = !!app.id;
  const m = openModal({ title: edit ? `Edit ${app.name}` : 'Add app', wide: true, submit: edit ? 'Save' : 'Create app',
    body: `<div class="two">${fld('App name *', `<input name="name" required maxlength="60" placeholder="e.g. Super Tool Pro" value="${esc(app.name || '')}">`)}
      ${fld('Category', `<select name="category_id">${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === app.category_id ? 'selected' : ''}>${esc(c.icon || '📁')} ${esc(c.name)}</option>`).join('')}</select>`)}</div>
      ${fld('Logo / image URL', `<input name="image_url" maxlength="400" placeholder="https://… (or upload with Broadcast ▸ upload)" value="${esc(app.image_url || '')}">`)}
      ${canSeeIds() ? `<div class="two">${fld('Video ID', `<input name="video_id" maxlength="400" value="${esc(app.video_id || '')}">`, 'Telegram file id of the preview video. Send the video to the bot to get its id.')}
      ${fld('Setup video ID', `<input name="setup_video_id" maxlength="400" value="${esc(app.setup_video_id || '')}">`, 'Sent to the user after a successful payment.')}</div>
      ${fld('App (APK) ID', `<input name="apk_id" maxlength="400" value="${esc(app.apk_id || '')}">`, 'Telegram file id of the APK — sent to the user together with the key.')}` : '<p class="muted tiny">🔒 File ids are only visible to the admin.</p>'}
      ${canSeeIds() ? '<div class="tiny muted" id="myFiles">📎 Files you sent to the bot: loading…</div>' : ''}
      ${fld('Short description *', `<input name="description" required maxlength="400" placeholder="What does the app do?" value="${esc(app.description || '')}">`)}
      ${fld('Features * (one per line)', `<textarea name="features" required rows="4" maxlength="1500" placeholder="e.g.&#10;Fast servers&#10;No ads">${esc(app.features || '')}</textarea>`)}`,
    onSubmit: async (d) => { const r = await api(edit ? `/apps/${app.id}` : '/apps', { method: edit ? 'PATCH' : 'POST', body: d }); toast(edit ? 'Saved ✨' : 'App created 🎉'); onDone(r); } });
  if (canSeeIds()) {  // chips of the files THIS admin sent to the bot; tap one to fill the id field you last clicked
    let last = 'apk_id';
    m.el.addEventListener('focusin', (e) => { if (['video_id', 'setup_video_id', 'apk_id'].includes(e.target.name)) last = e.target.name; });
    api('/my-files').then((r) => {
      const box = $('#myFiles', m.el); if (!box) return;
      box.innerHTML = '📎 Files you sent to the bot (tap to use): ' + (r.items.filter((f) => ['apk', 'video', 'document'].includes(f.kind)).slice(0, 10).map((f) => `<button type="button" class="chip" data-fill="${esc(f.file_id)}" data-kind="${esc(f.kind)}" title="${esc(f.file_id)}">${esc(f.name)}</button>`).join(' ') || (r.chat_linked ? 'none yet — send a file to the bot' : 'set your Telegram chat id in Settings first'));
    }).catch(() => {});
    m.el.addEventListener('click', (e) => {
      const c = e.target.closest('[data-fill]'); if (!c) return;
      const isVideo = c.dataset.kind === 'video', name = isVideo ? (last === 'apk_id' ? 'video_id' : last) : 'apk_id';
      m.form.elements[name].value = c.dataset.fill; toast(`Filled ${name.replace('_', ' ')}`);
    });
  }
  return m;
}

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>🛒 Purchase</h1><p>Purchase → Categories → Apps. Drag categories and apps to rank them. 🔥 Highlighted apps are always shown first; the rest follow your ranking, then best sellers.</p></div>
    <div class="row"><button class="btn w-only" id="addCat">＋ Category</button><button class="btn primary w-only" id="addApp">＋ Add app</button></div></div>
    <div id="cats" class="list">${skeleton(2)}</div><div id="body"></div>`;
  let cats = [], apps = [], dragging = null;
  async function load() {
    const [c, a] = await Promise.all([api('/categories'), api('/apps')]);
    cats = c.items; apps = a.apps;
    // same order as the shop: 🔥 highlighted first, then the admin's manual rank, then best sellers
    const rk = (a) => [a.highlighted ? 0 : 1, a.rank > 0 ? a.rank : 1e6, -(a.sales_count || 0), a.created_at || 0];
    const sort = (x) => x.sort((p, q) => { const a = rk(p), b = rk(q); for (let i = 0; i < 4; i++) if (a[i] !== b[i]) return a[i] - b[i]; return 0; });
    $('#cats', root).innerHTML = cats.length ? cats.map((k, i) => `<div class="li drag ${k.disabled ? 'dim' : ''}" draggable="true" data-cat="${esc(k.id)}"><div class="avatar">${esc(k.icon || '📁')}</div>
      <div class="meta"><b>#${i + 1} ${esc(k.name)} ${k.disabled ? '<span class="badge bad">hidden in bot</span>' : ''}</b><small>${k.apps} app${k.apps === 1 ? '' : 's'} · drag to reorder</small></div>
      <div class="acts"><label class="row tiny muted w-only" data-sw title="Show this whole category in the bot's Purchase menu">In bot <span class="switch"><input type="checkbox" data-catsw="${esc(k.id)}" ${k.disabled ? '' : 'checked'}><i></i></span></label><button class="btn sm w-only" data-edit="${esc(k.id)}">✏️</button><button class="btn sm danger w-only del-only" data-del="${esc(k.id)}">🗑</button></div></div>`).join('') : empty('📁', 'Create a category first (Apps, Games, Tools…).');
    const groups = cats.map((k) => [k, sort(apps.filter((x) => x.category_id === k.id))]);
    const known = new Set(cats.map((k) => k.id));
    const other = sort(apps.filter((x) => !known.has(x.category_id)));
    if (other.length) groups.push([{ id: '', name: 'Uncategorised', icon: '📦' }, other]);
    $('#body', root).innerHTML = groups.filter(([, l]) => l.length).map(([k, list]) => `<div class="section-title"><h2>${esc(k.icon || '📁')} ${esc(k.name)}</h2><span class="tiny muted">${list.length}</span></div>
      <div class="grid auto">${list.map((a) => `<div class="card app-card drag ${a.highlighted ? 'hot' : ''} ${a.sale_off ? 'dim' : ''}" draggable="true" data-rank-app="${esc(a.id)}" data-app="${esc(a.id)}" tabindex="0"><div class="top">${avatar(a.image_url, a.name, 'lg')}<div class="grow"><b>${esc(a.name)}</b><br><small class="muted">${a.seller_name ? '🚀 ' + esc(a.seller_name) : 'admin app'}</small></div>
        <span class="badge ${a.disabled || a.sale_off ? 'bad' : a.status === 'live' ? 'ok' : 'warn'}">${a.disabled ? 'disabled' : a.sale_off ? 'not selling' : a.status.replace('_', ' ')}</span></div>
        <div class="stats"><div><small>Purchases</small><b>${fmtNum(a.sales_count)}</b></div></div>
        <div class="row between"><label class="row tiny muted w-only" data-sw title="Show this app in the bot's Purchase menu">Selling <span class="switch"><input type="checkbox" data-sell="${esc(a.id)}" ${a.sale_off ? '' : 'checked'}><i></i></span></label><button class="btn sm w-only ${a.highlighted ? 'primary' : ''}" data-hl="${esc(a.id)}" data-on="${a.highlighted ? 0 : 1}">${a.highlighted ? '🔥 Highlighted' : '☆ Highlight'}</button><small class="muted">⇅ drag to rank</small></div></div>`).join('')}</div>`).join('') || '';
  }
  root.addEventListener('change', async (e) => {
    const t = e.target, cat = t.dataset.catsw, sell = t.dataset.sell;
    if (!cat && !sell) return;
    try {
      if (cat) await api(`/categories/${cat}/disable`, { method: 'POST', body: { disabled: !t.checked } });
      else await api(`/apps/${sell}/selling`, { method: 'POST', body: { on: t.checked } });
      toast(t.checked ? 'Now shown in the bot ✅' : 'Hidden from the bot 🙈'); load();
    } catch (er) { toast(er.message, 'err'); t.checked = !t.checked; }
  });
  root.addEventListener('click', async (e) => {
    if (e.target.closest('[data-sw]')) { e.stopPropagation(); return; }
    const hl = e.target.closest('[data-hl]');
    if (hl) { e.stopPropagation(); try { await api(`/apps/${hl.dataset.hl}/highlight`, { method: 'POST', body: { on: hl.dataset.on === '1' } }); toast(hl.dataset.on === '1' ? '🔥 App highlighted' : 'Highlight removed'); load(); } catch (er) { toast(er.message, 'err'); } return; }
    const ed = e.target.closest('[data-edit]'); const del = e.target.closest('[data-del]'); const app = e.target.closest('[data-app]');
    if (app) location.hash = '#/apps/' + app.dataset.app;
    if (ed) catForm(cats.find((k) => k.id === ed.dataset.edit));
    if (del) { const k = cats.find((x) => x.id === del.dataset.del); if (await confirmBox('Delete category?', `“${esc(k.name)}” disappears. Its apps move to Uncategorised.`, 'Delete')) { try { await api(`/categories/${k.id}`, { method: 'DELETE' }); load(); } catch (er) { toast(er.message, 'err'); } } }
  });
  function catForm(k = {}) {
    openModal({ title: k.id ? 'Edit category' : 'New category', body: `${fld('Name *', `<input name="name" required maxlength="30" placeholder="e.g. Games" value="${esc(k.name || '')}">`)}${fld('Icon (emoji) *', `<input name="icon" required maxlength="8" value="${esc(k.icon || '')}" placeholder="🎮">`)}`,
      onSubmit: async (d) => { await api(k.id ? `/categories/${k.id}` : '/categories', { method: k.id ? 'PATCH' : 'POST', body: { ...d, order: k.order || 0 } }); toast('Saved ✨'); load(); } });
  }
  const list = $('#cats', root);
  list.addEventListener('dragstart', (e) => { dragging = e.target.closest('[data-cat]'); if (dragging) { dragging.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragging.dataset.cat); } });
  list.addEventListener('dragover', (e) => { e.preventDefault(); const over = e.target.closest('[data-cat]'); if (dragging && over && over !== dragging) { const r = over.getBoundingClientRect(); over.parentNode.insertBefore(dragging, e.clientY > r.top + r.height / 2 ? over.nextSibling : over); } });
  list.addEventListener('dragend', async () => { if (!dragging) return; dragging.classList.remove('dragging'); dragging = null;
    try { await api('/categories/reorder', { method: 'POST', body: { ids: $$('[data-cat]', list).map((x) => x.dataset.cat) } }); toast('Ranking saved ✨'); load(); } catch (er) { toast(er.message, 'err'); } });
  // drag apps to rank them (inside their category); the whole ordering is saved at once
  const body = $('#body', root); let dragApp = null;
  body.addEventListener('dragstart', (e) => { dragApp = e.target.closest('[data-rank-app]'); if (dragApp) { dragApp.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragApp.dataset.rankApp); } });
  body.addEventListener('dragover', (e) => { e.preventDefault(); const over = e.target.closest('[data-rank-app]'); if (dragApp && over && over !== dragApp && over.parentNode === dragApp.parentNode) { const r = over.getBoundingClientRect(); over.parentNode.insertBefore(dragApp, e.clientX > r.left + r.width / 2 || e.clientY > r.top + r.height * 0.75 ? over.nextSibling : over); } });
  body.addEventListener('dragend', async () => { if (!dragApp) return; dragApp.classList.remove('dragging'); dragApp = null;
    try { await api('/apps/reorder', { method: 'POST', body: { ids: $$('[data-rank-app]', body).map((x) => x.dataset.rankApp) } }); toast('App ranking saved ✨'); load(); } catch (er) { toast(er.message, 'err'); } });
  $('#addCat', root).onclick = () => catForm();
  $('#addApp', root).onclick = () => { if (!cats.length) return toast('Create a category first', 'err'); appForm({ cats, onDone: (a) => { location.hash = '#/apps/' + a.id; } }); };
  await load();
}
