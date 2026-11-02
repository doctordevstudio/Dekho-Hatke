// Publish App — a step-by-step dialog (details → media → plans + free trial → APK → live APK testing → review).
// Everything is autosaved as a DRAFT on the server, so a seller who leaves half-way continues from the same step later.
import { $, api, avatar, canSeeIds, checkFile, confirmBox, copyText, empty, esc, fileLine, fld, fmtBytes, openModal, setBar, skeleton, state, toast, upload, ago } from '../core.js';

const STEPS = [
  { k: 'details', icon: '📝', t: 'App details', need: 'App name, category, short description and feature list — all required.' },
  { k: 'media', icon: '🖼', t: 'Logo & videos', need: 'Logo (PNG/JPG, max 2 MB) is uploaded here. Videos go through our Telegram bot: send the .mp4 to the bot as a video, copy the id it replies with and paste it. The preview video is required; the setup video sent to buyers after purchase is optional.' },
  { k: 'plans', icon: '🏷️', t: 'Plans & free trial', need: 'At least one plan (price + validity). The Free Trial plan is optional.' },
  { k: 'apk', icon: '📦', t: 'APK file id', need: 'Send your .apk to our Telegram bot as a file; it replies with a file id that only you can use. Paste the id here.' },
  { k: 'tests', icon: '🧪', t: 'Live APK testing', need: 'Create a test key, then prove: login works, a 2nd device is refused, maintenance mode and a revoked key are handled.' },
  { k: 'review', icon: '🚀', t: 'Review & submit', need: 'Check the summary and send it to our team. You are notified on Telegram.' },
];
const BADGE = { live: 'ok', in_verification: 'warn', rejected: 'bad', draft: 'info' };
const LABEL = { live: 'Live', in_verification: 'In verification', rejected: 'Rejected', draft: 'Draft' };
const SLOTS = {
  logo: { rule: 'image', accept: 'image/png,image/jpeg,image/webp', label: 'App logo *', hint: '.png / .jpg / .webp · max 2 MB', max: () => 2 * 1048576 },
  preview_video: { id: true, label: 'Preview video id *', hint: 'Send the .mp4 to the bot as a video (under 50 MB) — shown to buyers before they purchase', ph: 'Paste the video id the bot gave you' },
  setup_video: { id: true, label: 'Setup video id (sent to buyers after purchase)', hint: 'Send the .mp4 to the bot as a video (under 50 MB) — optional', ph: 'Paste the video id (optional)' },
  apk: { id: true, label: 'APK file id *', hint: 'Send the .apk to the bot as a file (only .apk, max ' + '${MAXAPK}' + ' MB)', ph: 'Paste the APK id the bot gave you' },
};

export default async function (root, preId) {
  root.innerHTML = `<div class="page-head"><div><h1>📤 Publish App</h1><p>Our team tests every app before it goes live. Start, leave and continue any time — your progress is saved as a draft.</p></div></div><div id="list">${skeleton(2)}</div>`;
  const list = $('#list', root);

  async function load() {
    let chk;
    try { chk = await api('/publish-check'); } catch (e) { list.innerHTML = empty('⚠️', e.message); return; }
    if (!chk.has_api) {
      list.innerHTML = `<div class="card"><div class="empty"><div class="big">🧩</div><b>Please create an app first</b><p class="muted">Create your API (Create API) — then come back here to publish it.</p><a class="btn primary" href="#/create">🧩 Create API</a></div></div>`;
      return;
    }
    list.innerHTML = `<div class="grid auto">${chk.apps.map((a) => `<div class="card stack" data-id="${esc(a.id)}"><div class="row">${avatar(a.image_url, a.name, 'lg')}<div class="grow"><b>${esc(a.name)}</b><br><span class="badge ${BADGE[a.status] || 'info'}">${esc(LABEL[a.status] || a.status)}</span>${a.has_draft ? ' <span class="badge warn">📝 draft saved</span>' : ''}</div></div>
      ${a.pending ? '<div class="seller-note">🕒 Under review — wait for the result before sending another update.</div>'
        : `<button class="btn primary" data-go="${esc(a.id)}">${a.has_draft ? '▶️ Continue draft' : a.status === 'live' ? '✏️ Update this app' : '🚀 Publish app'}</button>`}</div>`).join('')}</div>`;
    list.onclick = (e) => {
      const b = e.target.closest('[data-go]'); if (!b) return;
      openWizard(chk.apps.find((x) => x.id === b.dataset.go), load);
    };
    const pre = preId && chk.apps.find((a) => a.id === preId && !a.pending);
    if (pre) { preId = null; openWizard(pre, load); }
  }
  load();
}

// ------------------------------------------------------------------------------------------------ the wizard dialog
async function openWizard(appRow, done) {
  const id = appRow.id, B = `/apps/${id}`;
  let info;
  try { info = await api(`${B}/draft`); } catch (e) { return toast(e.message, 'err'); }
  if (info.pending) return toast('This app is under review. Wait for the result first.', 'err');
  const cats = state.me.categories;
  const data = { ...info.prefill, ...((info.draft && info.draft.data) || {}) };
  data.trial = { ...info.prefill.trial, ...(data.trial || {}) };
  if (!Array.isArray(data.plans) || !data.plans.length) data.plans = info.prefill.plans;
  const ups = { ...((info.draft && info.draft.uploads) || {}) };
  const resume = info.draft ? Math.max(1, info.draft.step || 1) : 1; // where "Continue draft" returns to
  let step = 0; // always open on the overview (0 = intro) so the seller sees every step and what is needed
  let hasDraft = !!info.draft, pollT = 0, saveT = 0, dead = false;
  let testState = { ready: false, checks: [], test_keys: [], events: [], maintenance: false };

  const m = openModal({ title: `Publish · ${appRow.name}`, wide: true, noFoot: true, body: '<div id="wz"></div>' });
  const wz = $('#wz', m.el);
  const stop = () => { dead = true; clearInterval(pollT); clearTimeout(saveT); };
  new MutationObserver((_, o) => { if (!document.body.contains(m.el)) { stop(); o.disconnect(); if (done) done(); } }).observe($('#modalRoot'), { childList: true });

  // ---- autosave
  const mark = (t) => { const el = $('#saved', wz); if (el) el.textContent = t; };
  const saveSoon = () => { clearTimeout(saveT); mark('…'); saveT = setTimeout(() => saveNow().catch(() => {}), 700); };
  async function saveNow(extra = {}) {
    clearTimeout(saveT);
    try { await api(`${B}/draft`, { method: 'PUT', body: { step: Math.max(1, step), data, ...extra } }); hasDraft = true; mark('💾 Draft saved'); }
    catch (e) { mark('⚠️ not saved'); throw e; }
  }

  // ---- helpers
  const idLine = (f) => (canSeeIds() && f && (f.file_id || f.media) ? ` · <span class="mono">ID ${esc(f.file_id || f.media)}</span>` : '');
  const botLink = () => (state.me.bot_username ? `<a href="https://t.me/${esc(state.me.bot_username)}" target="_blank" rel="noopener">@${esc(state.me.bot_username)}</a>` : 'our Telegram bot');
  const slot = (kind) => {
    const s = SLOTS[kind], cur = ups[kind];
    if (s.id) {
      const label = kind === 'apk' && info.has_live_apk ? 'New APK id (optional — leave empty to keep the live APK)' : s.label;
      const hint = s.hint.replace('${MAXAPK}', state.me.settings.max_apk_mb);
      return `<div class="upl ${cur ? 'done' : ''}" data-slot="${kind}" data-idslot><div><b>${label}</b><br><small class="muted">${hint} → ${botLink()}</small></div>
        <div class="row gap-top"><input class="grow mono" data-fid maxlength="400" autocomplete="off" spellcheck="false" placeholder="${esc(s.ph)}" value="${esc((cur && cur.file_id) || '')}"><button type="button" class="btn sm primary" data-check>Check</button></div>
        <div class="tiny" data-status>${cur ? '✅ ' + fileLine(cur.filename, cur.size) : ''}</div>
        <div class="tiny muted gap-top">📎 Your files sent to the bot: <span data-files="${kind}">loading…</span> <button type="button" class="btn sm" data-refresh="${kind}">↻</button></div></div>`;
    }
    const img = kind === 'logo' ? (cur ? cur.url : info.live_logo) : '';
    return `<div class="upl ${cur ? 'done' : ''}" data-slot="${kind}"><div class="row between"><div><b>${s.label}</b><br><small class="muted">${s.hint}</small></div>
      ${img ? `<img class="shot" src="${esc(img)}" alt="">` : ''}
      <label class="btn sm">${cur ? 'Replace' : 'Choose'}<input type="file" accept="${s.accept}" hidden></label></div>
      <div class="progress" hidden><div class="bar"><i></i></div><small></small></div>
      <div class="tiny" data-status>${cur ? '✅ ' + fileLine(cur.filename, cur.size) + idLine(cur) : ''}</div></div>`;
  };
  // the seller's own files (sent to the bot) as tap-to-use chips; only their ids, only theirs
  const myFiles = {};
  async function paintFiles(kind) {
    const el = $(`[data-files="${kind}"]`, wz); if (!el) return;
    try { myFiles[kind] = (await api('/files', { params: { kind } })).items; } catch { myFiles[kind] = []; }
    el.innerHTML = myFiles[kind].length ? myFiles[kind].slice(0, 8).map((f) => `<button type="button" class="chip" data-use="${esc(f.file_id)}" data-kind="${kind}" title="${esc(f.file_id)}">${esc(f.name)} · ${fmtBytes(f.size)}</button>`).join(' ') : 'none yet — send the file to the bot first';
  }
  const planRows = () => data.plans.map((p, i) => `<div class="plan-row" data-i="${i}"><label class="field"><span>Price ₹</span><input data-k="amount" type="number" min="1" placeholder="e.g. 99" value="${esc(p.amount)}"></label>
    <label class="field"><span>Validity</span><input data-k="validity_value" type="number" min="1" placeholder="e.g. 7" value="${esc(p.validity_value)}"></label>
    <label class="field"><span>Unit</span><select data-k="validity_unit">${['hours', 'days', 'years'].map((u) => `<option ${u === p.validity_unit ? 'selected' : ''}>${u}</option>`).join('')}</select></label>
    <label class="row"><input type="checkbox" data-k="is_best" ${p.is_best ? 'checked' : ''}> ⭐ best</label><button type="button" class="btn sm danger" data-rm="${i}">✕</button></div>`).join('');

  // ---- light validation per step (the server validates again)
  function problem(n) {
    if (n === 1) {
      if (!String(data.name || '').trim()) return 'Enter the app name.';
      if (!data.category_id && !String(data.new_category || '').trim()) return 'Choose a category or type a new one.';
      if (String(data.description || '').trim().length < 10) return 'Write a short description (at least 10 characters).';
      if (!String(data.features || '').trim()) return 'List at least one feature (one per line).';
    }
    if (n === 2) {
      if (!ups.logo && !info.live_logo) return 'Upload your app logo.';
      if (!ups.preview_video && !info.has_live_apk) return 'Add the preview video: send the .mp4 to the bot as a video and paste its id.';
    }
    if (n === 3) {
      if (!data.plans.length) return 'Add at least one plan.';
      for (const p of data.plans) if (!(+p.amount > 0) || !(+p.validity_value > 0)) return 'Every plan needs a price and a validity above zero.';
      if (data.trial.enabled && !(+data.trial.validity_value > 0)) return 'Set the free trial validity.';
    }
    if (n === 4 && !ups.apk && !info.has_live_apk) return 'Paste the APK file id: send your .apk to the bot and copy the id it replies with.';
    if (n === 5 && ups.apk && !testState.ready) return 'Finish the required live tests first (all 4 checks must be ticked).';
    return '';
  }

  // ---- rendering
  const stepper = () => `<div class="wz-steps">${STEPS.map((s, i) => `<button type="button" class="wz-dot ${step === i + 1 ? 'on' : step > i + 1 ? 'done' : ''}" data-jump="${i + 1}" title="${esc(s.t)}"><i>${step > i + 1 ? '✓' : i + 1}</i><span>${esc(s.t)}</span></button>`).join('')}</div>`;
  const nav = (last) => `<div class="wz-nav"><button type="button" class="btn" data-back>← Back</button><span class="muted tiny" id="saved"></span>
    ${last ? '<button type="button" class="btn primary" data-submit>🚀 Submit for review</button>' : '<button type="button" class="btn primary" data-next>Next →</button>'}</div>`;

  function draw() {
    clearInterval(pollT);
    if (step === 0) {
      wz.innerHTML = `<div class="stack"><p>Publishing takes <b>${STEPS.length} short steps</b>. Here is everything you need:</p>
        <ol class="wz-intro">${STEPS.map((s) => `<li><span class="ic">${s.icon}</span><div><b>${esc(s.t)}</b><br><small class="muted">${esc(s.need)}</small></div></li>`).join('')}</ol>
        <div class="seller-note">💾 Your progress is saved automatically as a <b>draft</b>. You can close this window at any time and continue later from <b>Publish App</b>.</div>
        <div class="row"><button type="button" class="btn primary" data-start>${hasDraft ? `▶️ Continue draft · step ${resume} of ${STEPS.length}` : '🚀 Start'}</button>
        ${hasDraft ? '<button type="button" class="btn danger" data-discard>🗑 Discard draft</button>' : ''}</div></div>`;
      return;
    }
    const s = STEPS[step - 1];
    let html;
    if (step === 1) {
      html = `<div class="two">${fld('App name *', `<input data-f="name" required maxlength="60" placeholder="e.g. Super Tool Pro" value="${esc(data.name)}">`)}${fld('Category *', `<select data-f="category_id"><option value="">— choose —</option>${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === data.category_id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>`)}</div>
        ${fld('…or create a new category', `<input data-f="new_category" maxlength="30" placeholder="e.g. Games — only if none of the above fits" value="${esc(data.new_category)}">`)}
        ${fld('Short description *', `<input data-f="description" maxlength="400" placeholder="What does your app do? (at least 10 characters)" value="${esc(data.description)}">`, 'Buyers read this before purchasing.')}
        ${fld('Features * (one per line)', `<textarea data-f="features" rows="5" maxlength="1500" placeholder="e.g.&#10;50+ fast servers&#10;One-tap connect&#10;No ads">${esc(data.features)}</textarea>`, 'Each line becomes a ✨ bullet in the bot.')}`;
    } else if (step === 2) {
      html = `${slot('logo')}${slot('preview_video')}${slot('setup_video')}`;
    } else if (step === 3) {
      html = `<p class="muted tiny">Set the price you want to earn for each validity (hours, days or years).</p><div id="plans">${planRows()}</div><button type="button" class="btn sm" data-addp>＋ Add plan</button>
        <div class="card stack gap-top"><label class="row"><span class="switch"><input type="checkbox" data-trial="enabled" ${data.trial.enabled ? 'checked' : ''}><i></i></span> <b>🎁 Free Trial plan</b></label>
        <p class="muted tiny">Each user can claim it once for your app. It costs the buyer nothing and does not earn you money.</p>
        <div class="plan-row" id="trialrow" ${data.trial.enabled ? '' : 'hidden'}><label class="field"><span>Trial validity</span><input data-trial="validity_value" type="number" min="1" placeholder="e.g. 1" value="${esc(data.trial.validity_value)}"></label>
        <label class="field"><span>Unit</span><select data-trial="validity_unit">${['hours', 'days'].map((u) => `<option ${u === data.trial.validity_unit ? 'selected' : ''}>${u}</option>`).join('')}</select></label></div></div>`;
    } else if (step === 4) {
      html = `${slot('apk')}<div class="seller-note">After the APK id is accepted you will test it live in the next step (login, device lock, maintenance, revoked key).</div>`;
    } else if (step === 5) {
      html = testsHtml();
    } else {
      html = reviewHtml();
    }
    wz.innerHTML = `${stepper()}<div class="stack wz-body"><h3>${s.icon} ${esc(s.t)}</h3><p class="muted tiny">${esc(s.need)}</p>${html}</div>${nav(step === 6)}`;
    if (step === 2) ['preview_video', 'setup_video'].forEach(paintFiles);
    if (step === 4) paintFiles('apk');
    if (step === 5) startPolling();
  }

  // ---- step 5: live testing
  function testsHtml() {
    if (!ups.apk) return '<div class="seller-note">✅ You did not attach a new APK, so no live test is needed — the current live APK keeps running. Continue to review.</div>';
    return `<div class="stack"><div class="card stack"><b>1 · Put your endpoint & API key in the app</b>
      <dl class="kv"><dt>Endpoint</dt><dd class="mono" id="t-ep"></dd><dt>API key</dt><dd class="mono" id="t-api"></dd></dl><div class="row"><button type="button" class="btn sm" data-cp="t-ep">Copy endpoint</button><button type="button" class="btn sm" data-cp="t-api">Copy API key</button></div></div>
      <div class="card stack"><b>2 · Create a test key and use it in your app</b>
      <div class="row"><label class="field"><span>Valid for (hours, 1–72)</span><input id="t-hours" type="number" min="1" max="72" placeholder="e.g. 6" value="6"></label><button type="button" class="btn primary" data-newkey>＋ Create test key</button></div><div id="t-keys"></div></div>
      <div class="card stack"><b>3 · Required checks</b><div id="t-checks"></div>
      <div class="row"><label class="row"><span class="switch"><input type="checkbox" id="t-maint"><i></i></span> Maintenance mode for the test key</label><button type="button" class="btn sm" data-reset>↺ Reset results</button></div></div>
      <div class="card stack"><b>Recent calls from your app</b><div id="t-events"></div></div></div>`;
  }
  function paintTests() {
    if (step !== 5 || !ups.apk) return;
    const t = testState;
    const set = (sel, html) => { const el = $(sel, wz); if (el && el.innerHTML !== html) el.innerHTML = html; };
    const ep = $('#t-ep', wz); if (ep) { ep.textContent = t.endpoint || ''; $('#t-api', wz).textContent = t.api_key || ''; }
    set('#t-checks', t.checks.map((c) => `<div class="check-row ${c.passed ? 'ok' : ''}"><span class="ck">${c.passed ? '✅' : '⬜'}</span><div class="grow"><b>${esc(c.label)}</b>${c.required ? '' : ' <small class="muted">(optional)</small>'}<br><small class="muted">${esc(c.hint)}</small></div></div>`).join(''));
    set('#t-keys', t.test_keys.length ? t.test_keys.map((k) => `<div class="check-row"><div class="grow"><span class="mono">${esc(k.key)}</span> <span class="badge ${k.status === 'active' ? 'ok' : 'bad'}">${esc(k.status)}</span><br><small class="muted">${k.bound ? '📲 bound to ' + esc(k.device) : 'not used yet'}</small></div>
      <div class="row"><button type="button" class="btn sm" data-copyk="${esc(k.key)}">Copy</button><button type="button" class="btn sm" data-ka="reset-device" data-key="${esc(k.key)}">Reset device</button><button type="button" class="btn sm" data-ka="expire" data-key="${esc(k.key)}">Expire</button><button type="button" class="btn sm danger" data-ka="revoke" data-key="${esc(k.key)}">Revoke</button><button type="button" class="btn sm" data-ka="restore" data-key="${esc(k.key)}">Restore</button></div></div>`).join('') : '<p class="muted tiny">No test key yet.</p>');
    set('#t-events', t.events.length ? t.events.map((e) => `<div class="tiny"><span class="muted">${ago(e.ts)}</span> · ${esc(e.label)}${e.device ? ' · ' + esc(e.device) : ''}</div>`).join('') : '<p class="muted tiny">Nothing yet — open your app with the test key.</p>');
    const mt = $('#t-maint', wz); if (mt && mt !== document.activeElement) mt.checked = !!t.maintenance;
  }
  async function pollTests() {
    if (dead || step !== 5 || !ups.apk) return;
    try { testState = await api(`${B}/draft/tests`); paintTests(); } catch { /* keep polling */ }
  }
  function startPolling() { pollTests(); pollT = setInterval(() => { if (!document.body.contains(m.el)) return stop(); pollTests(); }, 3000); }

  // ---- step 6
  function reviewHtml() {
    const cat = cats.find((c) => c.id === data.category_id);
    const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
    const file = (k) => (ups[k] ? fileLine(ups[k].filename, ups[k].size) + idLine(ups[k]) : '<span class="muted">—</span>');
    const miss = ups.apk ? testState.checks.filter((c) => c.required && !c.passed).map((c) => c.label) : [];
    return `<dl class="kv">${row('App', esc(data.name))}${row('Category', esc(data.new_category || (cat && cat.name) || '—'))}${row('Description', esc(data.description || '—'))}
      ${row('Plans', data.plans.map((p) => `₹${esc(p.amount)} / ${esc(p.validity_value)} ${esc(p.validity_unit)}${p.is_best ? ' ⭐' : ''}`).join(' · '))}
      ${row('Free trial', data.trial.enabled ? `${esc(data.trial.validity_value)} ${esc(data.trial.validity_unit)}` : 'off')}
      ${row('Logo', ups.logo ? fileLine(ups.logo.filename, ups.logo.size) + idLine(ups.logo) : info.live_logo ? 'current logo kept' : '—')}${row('Preview video', file('preview_video'))}${row('Setup video', file('setup_video'))}
      ${row('APK', ups.apk ? file('apk') : 'current live APK kept')}${row('Live tests', !ups.apk ? 'not needed' : miss.length ? `<span class="neg">Missing: ${esc(miss.join(', '))}</span>` : '✅ all required checks passed')}</dl>`;
  }

  // ---- navigation
  async function go(n) {
    step = n;
    if (n >= 5) { try { testState = await api(`${B}/draft/tests`); } catch { /* ignore */ } }
    draw();
    if (n > 0) saveNow().catch(() => {});
  }
  const next = () => { const p = problem(step); if (p) return toast(p, 'err'); return go(step + 1); };
  async function submit(btn) {
    for (let n = 1; n <= 5; n++) { const p = problem(n); if (p) { toast(p, 'err'); return go(n); } }
    btn.disabled = true;
    try {
      await saveNow();
      const r = await api(`${B}/publish`, { method: 'POST', body: { name: data.name, category_id: data.category_id, new_category: data.new_category, description: data.description, features: data.features,
        plans: data.plans, trial: data.trial, apk_upload: ups.apk && ups.apk.id, preview_video_upload: ups.preview_video && ups.preview_video.id,
        setup_video_upload: ups.setup_video && ups.setup_video.id, logo_media: ups.logo && ups.logo.media } });
      stop(); hasDraft = false;
      wz.innerHTML = `<div class="empty"><div class="big">🕒</div><b>Your app is under review</b><p class="muted">${esc(r.message)}</p><button type="button" class="btn primary" data-x>Close</button></div>`;
    } catch (e) { toast(e.message, 'err'); btn.disabled = false; }
  }

  // ---- events
  wz.addEventListener('input', (e) => {
    const f = e.target.dataset.f;
    if (f) { data[f] = e.target.value; return saveSoon(); }
    const r = e.target.closest('[data-i]');
    if (r && e.target.dataset.k) { data.plans[+r.dataset.i][e.target.dataset.k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value; return saveSoon(); }
    const t = e.target.dataset.trial;
    if (t) {
      data.trial[t] = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
      if (t === 'enabled') { const row = $('#trialrow', wz); if (row) row.hidden = !e.target.checked; }
      return saveSoon();
    }
  });
  wz.addEventListener('change', async (e) => {
    if (e.target.matches('#t-maint')) {
      try { await api(`${B}/draft/maintenance`, { method: 'POST', body: { enabled: e.target.checked } }); pollTests(); }
      catch (er) { toast(er.message, 'err'); e.target.checked = !e.target.checked; }
      return;
    }
    if (e.target.matches('input[type=file]')) uploadFile(e.target);
    if (e.target.matches('[data-fid]')) checkId(e.target.closest('[data-slot]'));
  });
  // logo: a small image uploaded here
  async function uploadFile(input) {
    const box = input.closest('[data-slot]'), kind = box.dataset.slot, s = SLOTS[kind], file = input.files[0];
    const st = $('[data-status]', box), pr = $('.progress', box); if (!file) return;
    const bad = checkFile(file, s.rule, s.max());
    if (bad) { st.innerHTML = `<span class="neg">${esc(bad)}</span>`; toast(bad, 'err'); input.value = ''; return; }
    st.innerHTML = '⏳ ' + fileLine(file.name, file.size, file.type); pr.hidden = false; box.classList.remove('done');
    try {
      const d = await upload(`/uploads/${kind}`, file, (p, l, t) => { setBar(pr, p); $('small', pr).textContent = `${p}% · ${fmtBytes(l)} of ${fmtBytes(t)}`; });
      ups[kind] = { media: d.media, url: d.url, filename: d.filename || file.name, size: d.size || file.size };
      await saveNow({ uploads: { [kind]: d.media }, logo_name: file.name, logo_size: file.size });
      box.classList.add('done'); st.innerHTML = '✅ ' + fileLine(ups[kind].filename, ups[kind].size) + idLine(ups[kind]);
    } catch (er) { delete ups[kind]; st.innerHTML = `<span class="neg">${esc(er.message)}</span>`; toast(er.message, 'err'); }
    pr.hidden = true;
  }
  // APK / videos: the seller pastes the id the bot gave them; the server accepts only ids from THEIR OWN files
  async function checkId(box) {
    const kind = box.dataset.slot, st = $('[data-status]', box), val = $('[data-fid]', box).value.trim();
    if (!val) { if (ups[kind]) { delete ups[kind]; await saveNow({ uploads: { [kind]: '' } }); box.classList.remove('done'); st.innerHTML = ''; } return; }
    st.innerHTML = '⏳ Checking…';
    try {
      const d = await api('/files/check', { method: 'POST', body: { kind, file_id: val } });
      ups[kind] = { id: d.id, file_id: d.file_id, filename: d.filename, size: d.size };
      if (kind === 'apk') await api(`${B}/draft/checks/reset`, { method: 'POST' }).catch(() => {}); // a new build must be tested again
      await saveNow({ uploads: { [kind]: d.id } });
      box.classList.add('done'); st.innerHTML = '✅ ' + fileLine(d.filename, d.size);
    } catch (er) { delete ups[kind]; box.classList.remove('done'); st.innerHTML = `<span class="neg">${esc(er.message)}</span>`; toast(er.message, 'err'); }
  }
  wz.addEventListener('click', async (e) => {
    const t = e.target;
    if (t.closest('[data-check]')) return checkId(t.closest('[data-slot]'));
    const use = t.closest('[data-use]');
    if (use) { const box = use.closest('[data-slot]'); $('[data-fid]', box).value = use.dataset.use; return checkId(box); }
    const rf = t.closest('[data-refresh]'); if (rf) { const el = $(`[data-files="${rf.dataset.refresh}"]`, wz); if (el) el.textContent = 'loading…'; return paintFiles(rf.dataset.refresh); }
    if (t.closest('[data-start]')) return go(resume);
    if (t.closest('[data-discard]')) {
      if (await confirmBox('Discard this draft?', 'Your saved progress and test keys for this app will be removed.', 'Discard')) { await api(`${B}/draft`, { method: 'DELETE' }); stop(); m.close(); toast('Draft discarded'); }
      return;
    }
    if (t.closest('[data-back]')) return go(step <= 1 ? 0 : step - 1);
    if (t.closest('[data-next]')) return next();
    if (t.closest('[data-submit]')) return submit(t.closest('[data-submit]'));
    const j = t.closest('[data-jump]');
    if (j) {
      const n = +j.dataset.jump;
      for (let k = step; k < n; k++) { const p = problem(k); if (p) { toast(p, 'err'); return go(k); } }
      return go(n);
    }
    if (t.closest('[data-addp]')) { if (data.plans.length >= 8) return toast('Maximum 8 plans', 'err'); data.plans.push({ amount: '499', validity_value: '90', validity_unit: 'days', is_best: false }); $('#plans', wz).innerHTML = planRows(); return saveSoon(); }
    const rm = t.closest('[data-rm]'); if (rm) { data.plans.splice(+rm.dataset.rm, 1); $('#plans', wz).innerHTML = planRows(); return saveSoon(); }
    const cp = t.closest('[data-cp]'); if (cp) { copyText($('#' + cp.dataset.cp, wz).textContent); return toast('Copied'); }
    const ck = t.closest('[data-copyk]'); if (ck) { copyText(ck.dataset.copyk); return toast('Key copied'); }
    if (t.closest('[data-newkey]')) {
      try { const r = await api(`${B}/draft/test-key`, { method: 'POST', body: { hours: +$('#t-hours', wz).value || 6 } }); copyText(r.key); toast('Test key created and copied'); pollTests(); }
      catch (er) { toast(er.message, 'err'); }
      return;
    }
    const ka = t.closest('[data-ka]');
    if (ka) { try { await api(`${B}/draft/test-keys/${ka.dataset.key}/${ka.dataset.ka}`, { method: 'POST' }); toast('Done'); pollTests(); } catch (er) { toast(er.message, 'err'); } return; }
    if (t.closest('[data-reset]')) { await api(`${B}/draft/checks/reset`, { method: 'POST' }); toast('Results cleared'); pollTests(); }
  });

  draw();
}
