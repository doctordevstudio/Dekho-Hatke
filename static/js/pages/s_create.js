// Create API: name, logo, category (existing or typed) → endpoint + API key + how to use.
import { $, api, copyText, esc, fld, state, toast } from '../core.js';

export default async function (root) {
  const cats = state.me.categories;
  root.innerHTML = `<div class="page-head"><div><h1>🧩 Create API</h1><p>Create the app entry first. You get an endpoint and API key to put inside your app. Publish it afterwards.</p></div></div>
    <div class="split"><div class="card"><form id="f" class="stack">${fld('App name', '<input name="name" required maxlength="60" placeholder="My Awesome App">')}${fld('Logo', '<input type="file" name="logo" accept="image/png,image/jpeg,image/webp">', 'PNG / JPG, max 2 MB.')}
      ${fld('Category', `<select name="category_id"><option value="">— choose existing —</option>${cats.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select>`)}${fld('…or type a new category', '<input name="new_category" maxlength="30" placeholder="e.g. Games">', 'Typing here creates a new category.')}
      <button class="btn primary">🧩 Create API</button></form></div><div class="card" id="out"><h3>How it works</h3><ol class="muted"><li>Create the API → copy endpoint + API key.</li><li>Call the endpoint from your app (see <a href="#/docs">Documentation</a>).</li><li><a href="#/publish">Publish</a> the app: logo, plans, APK and videos. Our team reviews it.</li></ol></div></div>`;
  $('#f', root).onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target); if (fd.get('new_category')) fd.set('category_id', '');
    const btn = e.submitter; btn.disabled = true;
    try { const a = await api('/apps', { method: 'POST', form: fd }); toast('API created 🎉');
      $('#out', root).innerHTML = `<h3>✅ ${esc(a.name)} is ready</h3><dl class="kv"><dt>Endpoint</dt><dd class="mono">${esc(a.endpoint)}</dd><dt>API key</dt><dd class="mono">${esc(a.api_key)}</dd></dl>
        <div class="row"><button class="btn sm" data-copy-text="${esc(a.endpoint)}">Copy endpoint</button><button class="btn sm" data-copy-text="${esc(a.api_key)}">Copy API key</button></div>
        <p class="muted tiny gap-top">Keep the API key secret — it is also visible later in <b>My Apps</b>. Next: <a href="#/publish/${esc(a.id)}">publish this app →</a></p>`;
      e.target.reset(); } catch (er) { toast(er.message, 'err'); } btn.disabled = false;
  };
}
