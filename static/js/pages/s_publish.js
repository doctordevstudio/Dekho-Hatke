// Publish App: must own an API app. Name, category, preview video (<50 MB), setup video, logo, plans (hours/days/years) + APK with progress.
import { $, $$, api, empty, esc, fld, fmtBytes, setBar, state, toast, upload } from '../core.js';

export default async function (root, preId) {
  const chk = await api('/publish-check');
  if (!chk.has_api) {
    root.innerHTML = `<div class="page-head"><div><h1>📤 Publish App</h1></div></div><div class="card"><div class="empty"><div class="big">🧩</div><b>Please create an app first</b><p class="muted">Create your API (Create API) — then come back here to publish it.</p><a class="btn primary" href="#/create">🧩 Create API</a></div></div>`;
    return;
  }
  const cats = state.me.categories, max = state.me.settings.max_apk_mb;
  let appId = preId || (chk.apps.find((a) => !a.pending) || chk.apps[0]).id;
  const up = {}; // kind -> upload id
  let plans = [{ amount: 99, validity_value: 7, validity_unit: 'days' }, { amount: 299, validity_value: 30, validity_unit: 'days', is_best: true }];

  root.innerHTML = `<div class="page-head"><div><h1>📤 Publish App</h1><p>Our team tests every app before it goes live. You will be notified on Telegram.</p></div></div>
    <div class="card stack"><label class="field"><span>Which of your apps?</span><select id="app">${chk.apps.map((a) => `<option value="${esc(a.id)}" ${a.id === appId ? 'selected' : ''}>${esc(a.name)} — ${esc(a.status.replace('_', ' '))}${a.pending ? ' (under review)' : ''}</option>`).join('')}</select></label><div id="warn"></div></div>
    <div id="formbox"></div>`;

  async function drawForm() {
    const f = await api(`/apps/${appId}/publish-form`);
    const a = f.app; plans = f.plans.length ? f.plans.map((p) => ({ ...p })) : plans;
    const live = a.status === 'live';
    $('#warn', root).innerHTML = chk.apps.find((x) => x.id === appId).pending ? '<div class="seller-note">🕒 This app is already under review. Wait for the result before sending another update.</div>' : live ? '<div class="seller-note">This app is live. Any change you submit goes to review again, while the current version keeps selling.</div>' : '';
    $('#formbox', root).innerHTML = `<form id="f" class="stack gap-top"><div class="card stack"><h3>📝 App details</h3>
      <div class="two">${fld('App name', `<input name="name" required maxlength="60" value="${esc(a.name)}">`)}${fld('Category', `<select name="category_id">${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === a.category_id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>`)}</div>
      ${fld('…or new category', '<input name="new_category" maxlength="30" placeholder="type to create a new one">')}${fld('Short description', `<input name="description" maxlength="400" value="${esc(a.description)}">`)}${fld('Features (one per line)', `<textarea name="features" rows="4" maxlength="1500">${esc(a.features)}</textarea>`)}</div>
      <div class="card stack"><h3>🖼 Files</h3>${slot('logo', 'App logo', 'image/png,image/jpeg,image/webp', '.png / .jpg · max 2 MB', a.image_url)}${slot('preview_video', 'Preview video', 'video/mp4', `.mp4 · less than 50 MB`)}${slot('setup_video', 'Setup video (sent after purchase)', 'video/mp4', '.mp4 · less than 50 MB')}${slot('apk', a.has_apk ? 'New APK (optional — leave empty to keep the live APK)' : 'APK file', '.apk', `.apk · up to ${max} MB (over 45 MB is stored securely and handed to the team)`)}</div>
      <div class="card stack"><h3>🏷️ Plans</h3><p class="muted tiny">Set the price you want to earn for each validity. Hours, days or years.</p><div id="plans"></div><button type="button" class="btn sm" id="addp">＋ Add plan</button></div>
      <div class="row"><button class="btn primary" id="go">🚀 Submit for review</button></div></form>`;
    drawPlans(); wire();
  }
  const slot = (kind, label, accept, hint, current) => `<div class="upl" data-slot="${kind}"><div class="row between"><div><b>${label}</b><br><small class="muted">${hint}</small></div>${current ? `<img class="shot" src="${esc(current)}" alt="">` : ''}<label class="btn sm">Choose<input type="file" accept="${accept}" hidden></label></div>
    <div class="progress" hidden><div class="bar"><i></i></div><small></small></div><div class="tiny" data-status></div></div>`;
  const drawPlans = () => { $('#plans', root).innerHTML = plans.map((p, i) => `<div class="plan-row" data-i="${i}"><label class="field"><span>Price ₹</span><input data-k="amount" type="number" min="1" value="${esc(p.amount)}"></label><label class="field"><span>Validity</span><input data-k="validity_value" type="number" min="1" value="${esc(p.validity_value)}"></label>
    <label class="field"><span>Unit</span><select data-k="validity_unit">${['hours', 'days', 'years'].map((u) => `<option ${u === p.validity_unit ? 'selected' : ''}>${u}</option>`).join('')}</select></label><label class="row"><input type="checkbox" data-k="is_best" ${p.is_best ? 'checked' : ''}> ⭐ best</label><button type="button" class="btn sm danger" data-rm="${i}">✕</button></div>`).join(''); };

  function wire() {
    $('#plans', root).oninput = (e) => { const r = e.target.closest('[data-i]'); if (r) plans[+r.dataset.i][e.target.dataset.k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value; };
    $('#plans', root).onclick = (e) => { const r = e.target.closest('[data-rm]'); if (r) { plans.splice(+r.dataset.rm, 1); drawPlans(); } };
    $('#addp', root).onclick = () => { if (plans.length >= 8) return toast('Maximum 8 plans', 'err'); plans.push({ amount: 499, validity_value: 90, validity_unit: 'days' }); drawPlans(); };
    $$('[data-slot]', root).forEach((s) => {
      const kind = s.dataset.slot, input = $('input[type=file]', s), pr = $('.progress', s), st = $('[data-status]', s);
      input.onchange = async () => {
        const file = input.files[0]; if (!file) return;
        const limit = kind === 'apk' ? max * 1048576 : kind === 'logo' ? 2097152 : 50 * 1048576;
        if (file.size > limit) { toast(`Too large (max ${fmtBytes(limit)})`, 'err'); input.value = ''; return; }
        pr.hidden = false; s.classList.remove('done'); st.textContent = '';
        try {
          const d = await upload(`/uploads/${kind}`, file, (p, l, t) => { setBar(pr, p); $('small', pr).textContent = `${p}% · ${fmtBytes(l)} of ${fmtBytes(t)}`; });
          up[kind] = kind === 'logo' ? d.media : d.id; s.classList.add('done'); st.innerHTML = `✅ ${esc(file.name)} uploaded${d.big ? ' — large file, stored for the team to review' : ''}`;
        } catch (e) { delete up[kind]; st.innerHTML = `<span class="neg">${esc(e.message)}</span>`; toast(e.message, 'err'); }
        pr.hidden = true;
      };
    });
    $('#f', root).onsubmit = async (e) => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(e.target)); const btn = $('#go', root); btn.disabled = true;
      const body = { name: fd.name, category_id: fd.category_id, new_category: fd.new_category, description: fd.description, features: fd.features, plans: plans.map((p) => ({ ...p, amount: p.amount, validity_value: p.validity_value })),
        apk_upload: up.apk, preview_video_upload: up.preview_video, setup_video_upload: up.setup_video, logo_media: up.logo };
      try { const r = await api(`/apps/${appId}/publish`, { method: 'POST', body }); $('#formbox', root).innerHTML = `<div class="card"><div class="empty"><div class="big">🕒</div><b>Your app is under review</b><p class="muted">${esc(r.message)}</p><a class="btn" href="#/apps">My Apps</a></div></div>`; $('#warn', root).innerHTML = ''; }
      catch (er) { toast(er.message, 'err'); btn.disabled = false; }
    };
  }
  $('#app', root).onchange = (e) => { appId = e.target.value; Object.keys(up).forEach((k) => delete up[k]); drawForm(); };
  window.onbeforeunload = null;
  drawForm();
}
