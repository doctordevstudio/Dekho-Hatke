// Seller broadcast: message everyone who bought your apps (or only selected apps). The admin approves before it is sent.
import { $, api, empty, esc, fld, fmtDate, fmtNum, pager, skeleton, state, tabsBar, toast } from '../core.js';

const BADGE = { pending_approval: 'warn', draft: 'info', sending: 'warn', done: 'ok', rejected: 'bad', stopped: 'bad' };
const LABEL = { pending_approval: 'waiting for admin approval', draft: 'approved', sending: 'sending', done: 'delivered', rejected: 'rejected', stopped: 'stopped' };

export default async function (root) {
  let tab = 'new', page = 1, apps = (await api('/apps')).items;
  root.innerHTML = `<div class="page-head"><div><h1>📣 Broadcast</h1><p>Send an announcement to the customers who bought your apps. Every broadcast is checked by the admin first.</p></div></div><div id="tb"></div><div id="main" class="gap-top"></div>`;
  $('#tb', root).appendChild(tabsBar([['new', '✍️ New'], ['history', '🗂 History']], tab, (k) => { tab = k; page = 1; draw(); }).el);
  async function draw() {
    const box = $('#main', root);
    if (tab === 'history') {
      box.innerHTML = skeleton(3);
      const d = await api('/broadcasts', { params: { page } });
      box.innerHTML = `<div class="list">${d.items.length ? d.items.map((b) => `<div class="li wide"><div class="avatar">📣</div><div class="meta"><b>${esc((b.message.text || '[media]').replace(/<[^>]+>/g, '').slice(0, 90))}</b><small>${fmtDate(b.created_at)}</small>
        <div class="chips"><span class="badge ${BADGE[b.status]}">${LABEL[b.status] || b.status}</span><span class="badge ok">✅ ${fmtNum(b.sent)} / ${fmtNum(b.total)}</span></div>${b.reject_reason ? `<small class="neg">${esc(b.reject_reason)}</small>` : ''}</div></div>`).join('') : empty('📣', 'No broadcasts yet.')}</div><div id="pg"></div>`;
      pager($('#pg', box), d, (p) => { page = p; draw(); });
      return;
    }
    box.innerHTML = `<form id="f" class="card stack"><div><span class="lbl-sm">Send to customers of</span><div class="chips">${apps.map((a) => `<label class="chip"><input type="checkbox" name="app" value="${esc(a.id)}"> ${esc(a.name)}</label>`).join('') || '<span class="muted">No apps yet</span>'}</div><small class="muted">Nothing ticked = all your apps.</small></div>
      <div class="seg" id="seg"><button type="button" data-f="html" class="on">HTML</button><button type="button" data-f="text">Plain text</button></div>
      <textarea name="text" rows="6" required maxlength="1000" placeholder="Write your message… &lt;b&gt;bold&lt;/b&gt;"></textarea>
      <div class="two">${fld('Image URL (optional, https)', '<input name="img" maxlength="400" placeholder="https://…">')}<div></div></div>
      <div class="two">${fld('Button text (optional)', '<input name="bt" maxlength="40">')}${fld('Button URL', '<input name="bu" maxlength="400" placeholder="https://…">')}</div>
      <div class="row between"><span class="badge info" id="aud">audience: …</span><button class="btn primary">📤 Send for approval</button></div></form>`;
    let fmt = 'html';
    $('#seg', box).onclick = (e) => { const b = e.target.closest('[data-f]'); if (b) { fmt = b.dataset.f; box.querySelectorAll('[data-f]').forEach((x) => x.classList.toggle('on', x === b)); } };
    const aud = async () => { const ids = [...box.querySelectorAll('[name=app]:checked')].map((i) => i.value); try { const d = await api('/broadcasts/audience', { params: { apps: ids.join(',') } }); $('#aud', box).textContent = `audience: ${fmtNum(d.count)} customer${d.count === 1 ? '' : 's'}`; } catch { /* ignore */ } };
    box.querySelector('.chips').onchange = aud; aud();
    $('#f', box).onsubmit = async (e) => { e.preventDefault(); const f = e.target.elements; const ids = [...box.querySelectorAll('[name=app]:checked')].map((i) => i.value);
      const message = { text: f.text.value, format: fmt, media: f.img.value.trim() ? { kind: 'photo', ref: f.img.value.trim() } : {}, button: f.bt.value || f.bu.value ? { text: f.bt.value, url: f.bu.value } : {} };
      try { const r = await api('/broadcasts', { method: 'POST', body: { message, app_ids: ids } }); toast(r.message); tab = 'history'; draw(); } catch (er) { toast(er.message, 'err'); } };
  }
  draw();
}
