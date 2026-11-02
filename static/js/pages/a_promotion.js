// Promotion (admin): pick an app, pick who gets it (everyone / chosen users / a number of random users), send the app card with its plans as buttons.
import { $, api, avatar, debounce, empty, esc, fld, fmtDate, fmtNum, pager, confirmBox, skeleton, toast } from '../core.js';

export default async function (root) {
  let app = null, mode = 'all', picked = new Map(), upage = 1, uq = '';
  root.innerHTML = `<div class="page-head"><div><h1>🔥 Promotion</h1><p>Choose an app, choose who receives it, and send the app's plans as buttons — users buy straight from the message.</p></div></div>
    <div class="card stack"><h3>1 · Select app</h3><div id="apps" class="grid auto">${skeleton(1)}</div></div>
    <div class="card stack gap-top" id="aud" hidden><h3>2 · Select users</h3>
      <div class="seg" id="mode"><button data-m="all" class="on">👥 All users</button><button data-m="users">🎯 Choose users</button><button data-m="count">🔢 Number of users</button></div>
      <div id="audbody"></div></div>
    <div class="card stack gap-top" id="send" hidden><h3>3 · Preview &amp; send</h3><div id="prev"></div>${fld('Extra line on top (optional)', '<input id="extra" maxlength="300" placeholder="🔥 Flash sale — only today!">')}
      <div class="row"><button class="btn primary w-only" id="go">🔥 Send promotion</button><span class="muted tiny" id="who"></span></div></div>
    <div class="section-title"><h2>🗂 Recent promotions</h2></div><div id="hist" class="list"></div>`;

  async function loadApps() {
    const d = await api('/promotions/apps');
    $('#apps', root).innerHTML = d.items.length ? d.items.map((a) => `<button type="button" class="card app-pick ${app && app.id === a.id ? 'hot' : ''}" data-app="${esc(a.id)}"><div class="row">${avatar(a.image_url, a.name, 'lg')}<div class="grow left"><b>${esc(a.name)}</b>${a.highlighted ? ' 🔥' : ''}<br><small class="muted">${a.plans} plan${a.plans === 1 ? '' : 's'}</small></div></div></button>`).join('') : empty('📱', 'No live app with plans yet.');
  }
  async function selectApp(id) {
    app = { id };
    await loadApps();
    $('#aud', root).hidden = false; $('#send', root).hidden = false;
    paintAud(); await paintPreview();
    $('#aud', root).scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  async function paintPreview() {
    try {
      const d = await api('/promotions/preview', { params: { app_id: app.id } });
      app.everyone = d.everyone;
      $('#prev', root).innerHTML = `<div class="promo-prev">${d.message.media && d.message.media.ref ? `<img class="shot" src="${esc(d.message.media.ref)}" alt="">` : ''}<div>${esc(String(d.message.text || '').replace(/<[^>]+>/g, ''))}</div>
        <div class="row gap-top">${d.buttons.map((b) => `<span class="badge info">${esc(b.text)}</span>`).join('')}</div></div>`;
      who();
    } catch (e) { $('#prev', root).innerHTML = empty('⚠️', e.message); }
  }
  function who() {
    const n = mode === 'all' ? app.everyone : mode === 'users' ? picked.size : +($('#cnt', root) || {}).value || 0;
    $('#who', root).textContent = mode === 'all' ? `Goes to ${fmtNum(app.everyone || 0)} users` : mode === 'users' ? `Goes to ${fmtNum(picked.size)} chosen user${picked.size === 1 ? '' : 's'}` : n ? `Goes to ${fmtNum(n)} random users` : 'Enter a number';
  }
  function paintAud() {
    const b = $('#audbody', root);
    if (mode === 'all') b.innerHTML = `<p class="muted">Every user who can receive messages (blocked users are skipped).</p>`;
    else if (mode === 'count') b.innerHTML = `${fld('How many users?', '<input id="cnt" type="number" min="1" max="5000" value="100">', 'A random set of this many users is chosen when you press send.')}`;
    else { b.innerHTML = `<label class="search"><span>🔎</span><input id="uq" type="search" placeholder="Search name, @username or chat id" value="${esc(uq)}" maxlength="64"></label><div class="chips" id="chosen"></div><div id="ulist" class="list"></div><div id="upg"></div>`; loadUsers(); drawChosen(); }
    who();
  }
  const drawChosen = () => { const c = $('#chosen', root); if (c) c.innerHTML = picked.size ? [...picked].map(([id, n]) => `<span class="chip" data-un="${esc(id)}">${esc(n)} ✕</span>`).join('') : '<span class="tiny muted">Nobody chosen yet.</span>'; };
  async function loadUsers() {
    const d = await api('/users', { params: { q: uq, page: upage } });
    $('#ulist', root).innerHTML = d.items.length ? d.items.filter((u) => !u.banned).map((u) => `<label class="li wide"><div class="row"><input type="checkbox" data-u="${esc(u.chat_id)}" data-n="${esc(u.full_name || u.chat_id)}" ${picked.has(u.chat_id) ? 'checked' : ''}>${avatar(u.profile_image_url, u.full_name, '')}</div><div class="meta"><b>${esc(u.full_name || '—')}</b><small>${u.username ? '@' + esc(u.username) : 'no username'} · <span class="mono">${esc(u.chat_id)}</span></small></div></label>`).join('') : empty('👻', 'No users found.');
    pager($('#upg', root), d, (p) => { upage = p; loadUsers(); });
  }
  async function history() {
    const d = await api('/broadcasts', { params: { page: 1 } });
    const rows = d.items.filter((b) => b.kind === 'promote');
    $('#hist', root).innerHTML = rows.length ? rows.map((b) => `<div class="li wide"><div class="avatar">🔥</div><div class="meta"><b>${esc(b.app_name || 'App')}</b><small>${fmtDate(b.created_at)} · ${esc(b.audience_label || 'All users')}</small>
      <div class="chips"><span class="badge ok">✅ ${fmtNum(b.sent)} sent</span>${b.failed ? `<span class="badge bad">${fmtNum(b.failed)} failed</span>` : ''}<span class="badge">of ${fmtNum(b.total)}</span><span class="badge ${b.status === 'done' ? 'ok' : 'warn'}">${esc(b.status)}</span></div></div></div>`).join('') : empty('🔥', 'No promotions yet.');
  }

  root.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-app]'); if (a) return selectApp(a.dataset.app);
    const m = e.target.closest('[data-m]');
    if (m) { mode = m.dataset.m; [...$('#mode', root).children].forEach((x) => x.classList.toggle('on', x === m)); return paintAud(); }
    const un = e.target.closest('[data-un]'); if (un) { picked.delete(un.dataset.un); drawChosen(); loadUsers(); return who(); }
    if (e.target.closest('#go')) {
      let audience;
      if (mode === 'all') audience = { type: 'all' };
      else if (mode === 'users') { if (!picked.size) return toast('Choose at least one user', 'err'); audience = { type: 'users', users: [...picked.keys()] }; }
      else { const n = +$('#cnt', root).value; if (!(n >= 1)) return toast('Enter how many users', 'err'); audience = { type: 'count', count: n }; }
      if (!(await confirmBox('Send this promotion?', `It is sent right away to <b>${esc($('#who', root).textContent.replace('Goes to ', ''))}</b>.`, 'Send', false))) return;
      try { await api('/broadcasts/promote', { method: 'POST', body: { app_id: app.id, text: $('#extra', root).value, audience } }); toast('Promotion started 🚀'); history(); }
      catch (er) { toast(er.message, 'err'); }
    }
  });
  root.addEventListener('change', (e) => {
    const u = e.target.closest('[data-u]');
    if (u) { u.checked ? picked.set(u.dataset.u, u.dataset.n) : picked.delete(u.dataset.u); drawChosen(); who(); }
  });
  root.addEventListener('input', debounce((e) => { if (e.target.id === 'uq') { uq = e.target.value.trim(); upage = 1; loadUsers(); } if (e.target.id === 'cnt') who(); }, 250));
  await loadApps(); history();
}
