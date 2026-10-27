// Create API: every detail is asked (only the support contact is optional) → endpoint + API key + how to use.
import { $, api, checkFile, copyText, esc, fld, state, toast } from '../core.js';

export default async function (root) {
  const cats = state.me.categories;
  root.innerHTML = `<div class="page-head"><div><h1>🧩 Create API</h1><p>Tell us about your app first. You get an endpoint and API key to put inside your app. Fields marked <b>*</b> are required.</p></div></div>
    <div class="split"><div class="card"><form id="f" class="stack" novalidate>
      ${fld('App name *', '<input name="name" required maxlength="60" placeholder="e.g. Super Tool Pro">', 'The name buyers see in the bot.')}
      ${fld('App logo *', '<input type="file" name="logo" required accept="image/png,image/jpeg,image/webp">', 'PNG / JPG / WEBP · real image · max 2 MB.')}
      <div class="two">${fld('Category *', `<select name="category_id"><option value="">— choose existing —</option>${cats.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select>`)}${fld('…or new category', '<input name="new_category" maxlength="30" placeholder="e.g. Games">', 'Typing here creates a new category.')}</div>
      ${fld('Short description *', '<textarea name="description" required rows="3" maxlength="400" placeholder="What does your app do? e.g. Unlimited VPN with 50+ servers and one-tap connect."></textarea>', 'At least 10 characters. Buyers read this before purchasing.')}
      ${fld('Android package name *', '<input name="package_name" required maxlength="120" placeholder="e.g. com.company.supertool" autocapitalize="off" spellcheck="false">', 'The applicationId of your APK (letters, numbers, dots).')}
      ${fld('Support contact (optional)', '<input name="contact" maxlength="80" placeholder="e.g. @your_support or support@example.com">', 'Shown to the team if buyers have problems.')}
      <button class="btn primary">🧩 Create API</button></form></div>
      <div class="card" id="out"><h3>How it works</h3><ol class="muted"><li>Create the API → copy endpoint + API key.</li><li>Call the endpoint from your app (see <a href="#/docs">Documentation</a>).</li><li><a href="#/publish">Publish</a> the app step by step: logo, videos, plans, APK and live tests. Our team reviews it.</li></ol></div></div>`;
  $('#f', root).onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target, fd = new FormData(f);
    const need = [['name', 'App name'], ['description', 'Short description'], ['package_name', 'Android package name']];
    for (const [k, label] of need) if (!String(fd.get(k) || '').trim()) return toast(`${label} is required.`, 'err');
    if (!fd.get('category_id') && !String(fd.get('new_category') || '').trim()) return toast('Choose a category or type a new one.', 'err');
    const logo = fd.get('logo');
    if (!logo || !logo.size) return toast('App logo is required.', 'err');
    const bad = checkFile(logo, 'image', 2 * 1048576); if (bad) return toast(bad, 'err');
    if (String(fd.get('description')).trim().length < 10) return toast('Short description: at least 10 characters.', 'err');
    if (fd.get('new_category')) fd.set('category_id', '');
    const btn = e.submitter; btn.disabled = true;
    try {
      const a = await api('/apps', { method: 'POST', form: fd }); toast('API created 🎉');
      $('#out', root).innerHTML = `<h3>✅ ${esc(a.name)} is ready</h3><dl class="kv"><dt>Endpoint</dt><dd class="mono">${esc(a.endpoint)}</dd><dt>API key</dt><dd class="mono">${esc(a.api_key)}</dd></dl>
        <div class="row"><button class="btn sm" data-copy-text="${esc(a.endpoint)}">Copy endpoint</button><button class="btn sm" data-copy-text="${esc(a.api_key)}">Copy API key</button></div>
        <p class="muted tiny gap-top">Keep the API key secret — it is also visible later in <b>My Apps</b>. Next: <a href="#/publish/${esc(a.id)}">publish this app →</a></p>`;
      f.reset();
    } catch (er) { toast(er.message, 'err'); }
    btn.disabled = false;
  };
}
