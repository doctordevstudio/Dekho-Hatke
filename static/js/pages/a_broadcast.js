// Broadcast (admin): compose text/HTML + media + optional URL button, preview + test, history with sent counts,
// edit / re-broadcast / delete (reflected in users' chats), "Promote app" with direct plan buttons, seller broadcasts to approve.
import { $, $$, api, confirmBox, empty, esc, fld, fmtDate, fmtNum, openModal, pager, segmented, skeleton, tabsBar, toast, upload, tick, fmtBytes } from '../core.js';

const BADGE = { draft: 'info', sending: 'warn', done: 'ok', stopped: 'bad', rejected: 'bad', pending_approval: 'violet' };

export default async function (root) {
  let tab = 'history', page = 1, mode = 'message', media = null, items = [], audience = 0;
  root.innerHTML = `<div class="page-head"><div><h1>📣 Broadcast</h1><p>Send to every user of the main bot. Edits and deletes also change the messages already in users’ chats.</p></div></div>
    <div id="tb"></div><div id="main" class="gap-top"></div>`;
  const tb = tabsBar([['history', '🗂 History'], ['new', '✍️ New message'], ['promote', '🔥 Promote app']], tab, (k) => { tab = k; page = 1; draw(); });
  $('#tb', root).appendChild(tb.el);

  async function draw() { if (tab === 'history') return history(); if (tab === 'new') return compose(); return promote(); }

  async function history() {
    const box = $('#main', root); box.innerHTML = skeleton(3);
    try {
      const d = await api('/broadcasts', { params: { page } });
      items = d.items; audience = d.audience;
      tb.setTabs([['history', '🗂 History', d.pending_approval], ['new', '✍️ New message'], ['promote', '🔥 Promote app']]);
      box.innerHTML = `<div class="list">${items.length ? items.map((b, i) => `<div class="li wide" data-i="${i}"><div class="avatar">${b.kind === 'promote' ? '🔥' : b.owner === 'admin' ? '📣' : '🚀'}</div>
        <div class="meta"><b>${esc((b.message.text || '[media]').replace(/<[^>]+>/g, '').slice(0, 90))}</b><small>${esc(b.owner_name)} · ${fmtDate(b.created_at)}${b.kind === 'promote' ? ' · promotes ' + esc(b.app_name || '') : ''}</small>
        <div class="chips"><span class="badge ${BADGE[b.status] || ''}">${b.status.replace('_', ' ')}</span><span class="badge ok">✅ ${fmtNum(b.sent)} sent</span>${b.failed ? `<span class="badge bad">${fmtNum(b.failed)} failed</span>` : ''}${b.blocked ? `<span class="badge warn">${fmtNum(b.blocked)} blocked bot</span>` : ''}<span class="badge">of ${fmtNum(b.total)}</span></div>
        ${b.status === 'pending_approval' ? `<small>Audience: ${b.audience.type === 'apps' ? 'buyers of the seller’s apps' : 'all'}</small>` : ''}${b.reject_reason ? `<small class="neg">${esc(b.reject_reason)}</small>` : ''}</div>
        <div class="acts">${b.status === 'pending_approval' ? `<button class="btn sm ok w-only" data-act="approve" data-i="${i}">Approve</button><button class="btn sm danger w-only" data-act="reject" data-i="${i}">Reject</button>` : ''}
          ${['done', 'stopped', 'sending'].includes(b.status) ? `<button class="btn sm w-only" data-act="edit" data-i="${i}">✏️ Edit</button><button class="btn sm w-only" data-act="again" data-i="${i}">🔁 Re-send</button>` : ''}
          ${b.status === 'sending' ? `<button class="btn sm danger w-only" data-act="stop" data-i="${i}">⏹</button>` : ''}<button class="btn sm danger w-only del-only" data-act="del" data-i="${i}">🗑</button></div></div>`).join('') : empty('📣', 'No broadcasts yet.')}</div><div id="pg"></div>`;
      pager($('#pg', box), d, (p) => { page = p; history(); });
      if (items.some((b) => b.status === 'sending')) setTimeout(() => tab === 'history' && document.body.contains(root) && history(), 4000);
    } catch (e) { toast(e.message, 'err'); }
  }

  function msgFields(m = {}) {
    return `<div class="seg"><button type="button" data-f="html" class="on">HTML</button><button type="button" data-f="text">Plain text</button></div>
      <textarea name="text" rows="7" maxlength="3900" placeholder="Write your message… &lt;b&gt;bold&lt;/b&gt; &lt;i&gt;italic&lt;/i&gt; &lt;a href=&quot;…&quot;&gt;link&lt;/a&gt;">${esc(m.text || '')}</textarea>
      <div class="upl" id="up"><div class="row"><label class="btn sm">📎 Photo / video / file<input type="file" id="file" hidden></label><span class="tiny muted" id="upname">${m.media && m.media.ref ? 'media attached (' + esc(m.media.kind) + ')' : 'optional · up to 49 MB'}</span><button type="button" class="btn sm" id="rm" ${m.media && m.media.ref ? '' : 'hidden'}>✕</button></div>
        <div class="progress" id="pr" hidden><div class="bar"><i></i></div><small></small></div>
        <input type="text" name="media_url" placeholder="…or paste an image URL / Telegram file id" class="gap-top" value="${esc(m.media && m.media.ref ? m.media.ref : '')}"></div>
      <div class="two">${fld('Button text (optional)', `<input name="btn_text" maxlength="40" value="${esc((m.button || {}).text || '')}">`)}${fld('Button URL', `<input name="btn_url" maxlength="400" placeholder="https://…" value="${esc((m.button || {}).url || '')}">`)}</div>`;
  }
  function wire(box, initial = {}) {
    let fmt = initial.format || 'html', kind = (initial.media || {}).kind || '', ref = (initial.media || {}).ref || '';
    box.addEventListener('click', (e) => { const f = e.target.closest('[data-f]'); if (f) { fmt = f.dataset.f; $$('[data-f]', box).forEach((x) => x.classList.toggle('on', x === f)); }
      if (e.target.closest('#rm')) { kind = ref = ''; $('[name=media_url]', box).value = ''; $('#upname', box).textContent = 'removed'; $('#rm', box).hidden = true; } });
    $('#file', box).addEventListener('change', async (ev) => {
      const f = ev.target.files[0]; if (!f) return;
      const pr = $('#pr', box); pr.hidden = false;
      try { const d = await upload('/broadcast/upload', f, (p, l, t) => { $('i', pr).style.width = p + '%'; $('small', pr).textContent = `${p}% · ${fmtBytes(l)} / ${fmtBytes(t)}`; });
        kind = d.kind; ref = d.ref; $('[name=media_url]', box).value = ''; $('#upname', box).textContent = `✅ ${d.name} (${d.kind})`; $('#rm', box).hidden = false; toast('Uploaded ✨'); } catch (er) { toast(er.message, 'err'); }
      pr.hidden = true; ev.target.value = ''; });
    return () => { const url = $('[name=media_url]', box).value.trim(); const mk = url ? { kind: kind || 'photo', ref: url } : ref ? { kind, ref } : {};
      const bt = $('[name=btn_text]', box).value.trim(), bu = $('[name=btn_url]', box).value.trim();
      return { text: $('[name=text]', box).value, format: fmt, media: mk, button: bt || bu ? { text: bt, url: bu } : {} }; };
  }

  function compose() {
    $('#main', root).innerHTML = `<div class="card stack" id="cmp"><h3>✍️ New broadcast</h3>${msgFields()}<div class="row"><button class="btn" id="test">🧪 Send test to me</button><button class="btn primary w-only" id="send">📣 Send to all users</button></div></div>`;
    const get = wire($('#cmp', root));
    $('#test', root).onclick = async () => { try { await api('/broadcasts/test', { method: 'POST', body: { message: get() } }); toast('Test sent to the admin chat'); } catch (e) { toast(e.message, 'err'); } };
    $('#send', root).onclick = async () => { if (await confirmBox('Send to everyone?', 'This goes to <b>all users</b> of the main bot right away.', 'Send', false)) { try { await api('/broadcasts', { method: 'POST', body: { message: get() } }); toast('Broadcast started 🚀'); tab = 'history'; tb.set('history'); draw(); } catch (e) { toast(e.message, 'err'); } } };
  }

  async function promote() {
    const { apps } = await api('/apps');
    const live = apps.filter((a) => a.status === 'live' && !a.disabled);
    $('#main', root).innerHTML = `<div class="card stack"><h3>🔥 Promote an app</h3><p class="muted tiny">Sends the app card with its plans as buttons — users pick a plan and buy straight from the message.</p>
      <form id="pf" class="stack">${fld('App', `<select name="app_id" required>${live.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}</select>`)}${fld('Extra line on top (optional)', '<input name="text" maxlength="300" placeholder="🔥 Flash sale — only today!">')}<button class="btn primary w-only">🔥 Promote now</button></form></div>`;
    $('#pf', root).onsubmit = async (e) => { e.preventDefault(); try { await api('/broadcasts/promote', { method: 'POST', body: Object.fromEntries(new FormData(e.target)) }); toast('Promotion started 🚀'); tab = 'history'; tb.set('history'); draw(); } catch (er) { toast(er.message, 'err'); } };
  }

  root.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-act]'); if (!a) return;
    const b = items[+a.dataset.i];
    try {
      if (a.dataset.act === 'approve') { await api(`/broadcasts/${b.id}/approve`, { method: 'POST' }); toast('Approved — sending'); history(); }
      else if (a.dataset.act === 'reject') openModal({ title: 'Reject broadcast', danger: true, submit: 'Reject', body: fld('Reason (sent to the seller)', '<input name="reason" required maxlength="300">'), onSubmit: async (d) => { await api(`/broadcasts/${b.id}/reject`, { method: 'POST', body: d }); history(); } });
      else if (a.dataset.act === 'stop') { await api(`/broadcasts/${b.id}/stop`, { method: 'POST' }); history(); }
      else if (a.dataset.act === 'del') { if (await confirmBox('Delete broadcast?', 'Also <b>removes the message from every chat</b> it was delivered to.', 'Delete')) { await api(`/broadcasts/${b.id}`, { method: 'DELETE' }); history(); } }
      else if (a.dataset.act === 'edit' || a.dataset.act === 'again') {
        const again = a.dataset.act === 'again';
        const m = openModal({ title: again ? 'Re-broadcast' : 'Edit broadcast', wide: true, submit: again ? 'Send again' : 'Save & update chats', body: `<div id="ef" class="stack">${msgFields(b.message)}</div>${again ? '<label class="row"><span class="switch"><input type="checkbox" name="delete_old"><i></i></span> Delete the old messages first</label>' : '<p class="tiny muted">Changes are applied to the messages already sent.</p>'}`,
          onSubmit: async (_d, form) => { if (again) await api(`/broadcasts/${b.id}/rebroadcast`, { method: 'POST', body: { message: get(), delete_old: form.elements.delete_old.checked } }); else await api(`/broadcasts/${b.id}`, { method: 'PATCH', body: { message: get() } }); toast(again ? 'Sending again 🚀' : 'Updated in chats ✨'); history(); } });
        var get = wire($('#ef', m.el), b.message);
      }
    } catch (er) { toast(er.message, 'err'); }
  });
  draw();
}
