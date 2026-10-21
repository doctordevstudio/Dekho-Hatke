// Purchase: Categories → Apps → (video, features, plans). Category ranking by drag & drop; apps inside are ordered by most purchased.
import { $, $$, api, avatar, confirmBox, empty, esc, fld, fmtNum, openModal, skeleton, toast } from '../core.js';

export function appForm({ app = {}, cats, onDone }) {
  const edit = !!app.id;
  return openModal({ title: edit ? `Edit ${app.name}` : 'Add app', wide: true, submit: edit ? 'Save' : 'Create app',
    body: `<div class="two">${fld('App name', `<input name="name" required maxlength="60" value="${esc(app.name || '')}">`)}
      ${fld('Category', `<select name="category_id">${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === app.category_id ? 'selected' : ''}>${esc(c.icon || '📁')} ${esc(c.name)}</option>`).join('')}</select>`)}</div>
      ${fld('Logo / image URL', `<input name="image_url" maxlength="400" placeholder="https://… (or upload with Broadcast ▸ upload)" value="${esc(app.image_url || '')}">`)}
      <div class="two">${fld('Video ID', `<input name="video_id" maxlength="400" value="${esc(app.video_id || '')}">`, 'Telegram file id of the preview video. Send the video to the bot to get its id.')}
      ${fld('Setup video ID', `<input name="setup_video_id" maxlength="400" value="${esc(app.setup_video_id || '')}">`, 'Sent to the user after a successful payment.')}</div>
      ${fld('App (APK) ID', `<input name="apk_id" maxlength="400" value="${esc(app.apk_id || '')}">`, 'Telegram file id of the APK — sent to the user together with the key.')}
      ${fld('Short description', `<input name="description" maxlength="400" value="${esc(app.description || '')}">`)}
      ${fld('Features (one per line)', `<textarea name="features" rows="4" maxlength="1500">${esc(app.features || '')}</textarea>`)}`,
    onSubmit: async (d) => { const r = await api(edit ? `/apps/${app.id}` : '/apps', { method: edit ? 'PATCH' : 'POST', body: d }); toast(edit ? 'Saved ✨' : 'App created 🎉'); onDone(r); } });
}

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>🛒 Purchase</h1><p>Purchase → Categories → Apps. Drag categories to rank them; apps in a category are shown most-purchased first.</p></div>
    <div class="row"><button class="btn w-only" id="addCat">＋ Category</button><button class="btn primary w-only" id="addApp">＋ Add app</button></div></div>
    <div id="cats" class="list">${skeleton(2)}</div><div id="body"></div>`;
  let cats = [], apps = [], dragging = null;
  async function load() {
    const [c, a] = await Promise.all([api('/categories'), api('/apps')]);
    cats = c.items; apps = a.apps;
    const sort = (x) => x.sort((p, q) => (q.sales_count || 0) - (p.sales_count || 0) || p.created_at - q.created_at);
    $('#cats', root).innerHTML = cats.length ? cats.map((k, i) => `<div class="li drag" draggable="true" data-cat="${esc(k.id)}"><div class="avatar">${esc(k.icon || '📁')}</div>
      <div class="meta"><b>#${i + 1} ${esc(k.name)}</b><small>${k.apps} app${k.apps === 1 ? '' : 's'} · drag to reorder</small></div>
      <div class="acts"><button class="btn sm w-only" data-edit="${esc(k.id)}">✏️</button><button class="btn sm danger w-only del-only" data-del="${esc(k.id)}">🗑</button></div></div>`).join('') : empty('📁', 'Create a category first (Apps, Games, Tools…).');
    const groups = cats.map((k) => [k, sort(apps.filter((x) => x.category_id === k.id))]);
    const known = new Set(cats.map((k) => k.id));
    const other = sort(apps.filter((x) => !known.has(x.category_id)));
    if (other.length) groups.push([{ id: '', name: 'Uncategorised', icon: '📦' }, other]);
    $('#body', root).innerHTML = groups.filter(([, l]) => l.length).map(([k, list]) => `<div class="section-title"><h2>${esc(k.icon || '📁')} ${esc(k.name)}</h2><span class="tiny muted">${list.length}</span></div>
      <div class="grid auto">${list.map((a) => `<div class="card app-card" data-app="${esc(a.id)}" tabindex="0"><div class="top">${avatar(a.image_url, a.name, 'lg')}<div class="grow"><b>${esc(a.name)}</b><br><small class="muted">${a.seller_name ? '🚀 ' + esc(a.seller_name) : 'admin app'}</small></div>
        <span class="badge ${a.disabled ? 'bad' : a.status === 'live' ? 'ok' : 'warn'}">${a.disabled ? 'disabled' : a.status.replace('_', ' ')}</span></div>
        <div class="stats"><div><small>Purchases</small><b>${fmtNum(a.sales_count)}</b></div></div></div>`).join('')}</div>`).join('') || '';
  }
  root.addEventListener('click', async (e) => {
    const ed = e.target.closest('[data-edit]'); const del = e.target.closest('[data-del]'); const app = e.target.closest('[data-app]');
    if (app) location.hash = '#/apps/' + app.dataset.app;
    if (ed) catForm(cats.find((k) => k.id === ed.dataset.edit));
    if (del) { const k = cats.find((x) => x.id === del.dataset.del); if (await confirmBox('Delete category?', `“${esc(k.name)}” disappears. Its apps move to Uncategorised.`, 'Delete')) { try { await api(`/categories/${k.id}`, { method: 'DELETE' }); load(); } catch (er) { toast(er.message, 'err'); } } }
  });
  function catForm(k = {}) {
    openModal({ title: k.id ? 'Edit category' : 'New category', body: `${fld('Name', `<input name="name" required maxlength="30" value="${esc(k.name || '')}">`)}${fld('Icon (emoji)', `<input name="icon" maxlength="8" value="${esc(k.icon || '')}" placeholder="🎮">`)}`,
      onSubmit: async (d) => { await api(k.id ? `/categories/${k.id}` : '/categories', { method: k.id ? 'PATCH' : 'POST', body: { ...d, order: k.order || 0 } }); toast('Saved ✨'); load(); } });
  }
  const list = $('#cats', root);
  list.addEventListener('dragstart', (e) => { dragging = e.target.closest('[data-cat]'); if (dragging) { dragging.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragging.dataset.cat); } });
  list.addEventListener('dragover', (e) => { e.preventDefault(); const over = e.target.closest('[data-cat]'); if (dragging && over && over !== dragging) { const r = over.getBoundingClientRect(); over.parentNode.insertBefore(dragging, e.clientY > r.top + r.height / 2 ? over.nextSibling : over); } });
  list.addEventListener('dragend', async () => { if (!dragging) return; dragging.classList.remove('dragging'); dragging = null;
    try { await api('/categories/reorder', { method: 'POST', body: { ids: $$('[data-cat]', list).map((x) => x.dataset.cat) } }); toast('Ranking saved ✨'); load(); } catch (er) { toast(er.message, 'err'); } });
  $('#addCat', root).onclick = () => catForm();
  $('#addApp', root).onclick = () => { if (!cats.length) return toast('Create a category first', 'err'); appForm({ cats, onDone: (a) => { location.hash = '#/apps/' + a.id; } }); };
  await load();
}
