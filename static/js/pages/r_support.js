// Reseller support: the text shown to your users when they tap Support (plain text / HTML) + the admin's help message.
import { $, api, esc, fld, toast } from '../core.js';

export default async function (root) {
  const d = await api('/support');
  root.innerHTML = `<div class="page-head"><div><h1>🛟 Support</h1><p>What your users see when they tap <b>Support</b> in your bot.</p></div></div>
    <div class="card"><form id="f" class="stack">${fld('Support @username', `<input name="username" maxlength="64" value="${esc(d.username)}" placeholder="your_support">`, 'A “Contact support” button opens a chat with this account.')}
    ${fld('Message (text or HTML)', `<textarea name="text" rows="8" maxlength="1500" placeholder="Need help? We reply within 24h…&#10;&lt;b&gt;bold&lt;/b&gt; &lt;a href=&quot;https://…&quot;&gt;link&lt;/a&gt;">${esc(d.text)}</textarea>`)}<button class="btn primary">Save</button></form></div>`;
  $('#f', root).onsubmit = async (e) => { e.preventDefault(); try { await api('/support', { method: 'PUT', body: Object.fromEntries(new FormData(e.target)) }); toast('Saved ✨'); } catch (er) { toast(er.message, 'err'); } };
}
