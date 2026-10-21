// Seller app review (admin): edit submission, download APK, enter Telegram ids, generate a TEST key and watch the LIVE CHECKS tick off
// from real API calls (login → device uniqueness → maintenance → revoke), then approve.
import { $, $$, api, avatar, confirmBox, copyText, empty, esc, fld, fmtBytes, fmtDate, fmtMoney, isSuper, openModal, skeleton, tick, toast, until } from '../core.js';

export default async function (root, subId) {
  root.innerHTML = skeleton(3);
  let d;
  try { d = await api(`/submissions/${subId}`); } catch (e) { root.innerHTML = empty('💥', e.message); return; }
  const pending = d.status === 'pending';
  const cats = (await api('/categories')).items;
  const F = d.files || {};
  const plansRows = (list) => list.map((p, i) => `<div class="plan-row" data-i="${i}"><label class="field"><span>Price ₹</span><input data-k="amount" type="number" min="1" value="${esc(p.amount)}"></label>
    <label class="field"><span>Validity</span><input data-k="validity_value" type="number" min="1" value="${esc(p.validity_value)}"></label>
    <label class="field"><span>Unit</span><select data-k="validity_unit">${['hours', 'days', 'years'].map((u) => `<option ${u === p.validity_unit ? 'selected' : ''}>${u}</option>`).join('')}</select></label>
    <label class="row"><input type="checkbox" data-k="is_best" ${p.is_best ? 'checked' : ''}> ⭐</label><button type="button" class="btn sm danger" data-rm="${i}">✕</button></div>`).join('');
  let plans = d.data.plans.map((p) => ({ ...p }));
  const fileBox = (kind, label, idKey, current) => {
    const f = F[kind];
    return `<div class="upl ${f && f.tg_file_id ? 'done' : ''}"><b>${label}</b> ${f ? `<span class="muted tiny">${esc(f.filename)} · ${fmtBytes(f.size)}</span>` : '<span class="muted tiny">not changed</span>'}
      ${f && f.in_storage ? `<a class="btn sm" href="/admin/api/submissions/${esc(subId)}/download/${kind}">⬇️ Download</a>` : ''}
      ${f && !f.in_storage && !f.tg_file_id ? '<span class="badge warn">not stored — ask the seller</span>' : ''}
      ${fld('Telegram file id', `<input data-id="${idKey}" maxlength="400" value="${esc((f && f.tg_file_id) || '')}" placeholder="${kind === 'apk' && f && f.size > 45 * 1048576 ? 'APK is over 45 MB: download it, send it to the bot, paste the id it returns' : 'captured automatically for small files'}">`, current ? `Live now: <span class="mono">${esc(String(current).slice(0, 28))}…</span>` : '')}</div>`;
  };
  root.innerHTML = `<div class="page-head"><div class="row">${avatar((F.logo && F.logo.url) || d.current.image_url, d.data.name, 'lg')}<div><h1>${esc(d.data.name)} <span class="badge ${d.kind === 'new' ? 'violet' : 'info'}">${d.kind === 'new' ? 'new app' : 'update'}</span></h1>
      <p>🚀 ${esc(d.seller_name)} · submitted ${fmtDate(d.created_at)} · <span class="badge ${{ pending: 'warn', approved: 'ok', rejected: 'bad' }[d.status]}">${d.status}</span></p></div></div><a class="btn" href="#/seller">← Back</a></div>
    ${d.reason ? `<div class="seller-note">Rejected: ${esc(d.reason)}</div>` : ''}
    <div class="split"><div class="stack">
      <div class="card stack"><h3>📝 Submitted details</h3>
        <div class="two">${fld('App name', `<input id="f_name" maxlength="60" value="${esc(d.data.name)}">`)}${fld('Category', `<select id="f_cat">${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === d.data.category_id ? 'selected' : ''}>${esc(c.icon || '📁')} ${esc(c.name)}</option>`).join('')}</select>`)}</div>
        ${fld('Description', `<input id="f_desc" maxlength="400" value="${esc(d.data.description || '')}">`)}${fld('Features', `<textarea id="f_feat" rows="4" maxlength="1500">${esc(d.data.features || '')}</textarea>`)}
        <h3>🏷️ Plans</h3><div id="plans">${plansRows(plans)}</div><button type="button" class="btn sm" id="addPlan">＋ Plan</button></div>
      <div class="card stack"><h3>📦 Files &amp; Telegram ids</h3>${F.apk ? '' : '<p class="muted tiny">No new APK in this submission — the live APK stays.</p>'}
        ${fileBox('apk', '📱 APK', 'apk_id', d.current.apk_id)}${fileBox('preview_video', '🎬 Preview video', 'video_id', d.current.video_id)}${fileBox('setup_video', '🛠 Setup video', 'setup_video_id', d.current.setup_video_id)}
        ${fld('Admin note (internal)', `<input id="f_note" maxlength="600" value="${esc(d.note || '')}">`)}
        <div class="row"><button class="btn primary w-only" id="save">💾 Save edits</button></div></div></div>
    <div class="stack"><div class="card stack" id="live"></div></div></div>
    ${pending ? `<div class="card gap-top row between"><div><b>Decision</b><p class="muted tiny" id="why"></p></div><div class="row"><button class="btn danger w-only" id="reject">❌ Reject</button><button class="btn ok w-only" id="approve" disabled>✅ Approve &amp; publish</button></div></div>` : ''}`;
  const drawPlans = () => { $('#plans', root).innerHTML = plansRows(plans); };
  $('#plans', root).addEventListener('input', (e) => { const row = e.target.closest('[data-i]'); if (!row) return; const k = e.target.dataset.k; plans[+row.dataset.i][k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value; });
  $('#plans', root).addEventListener('click', (e) => { const r = e.target.closest('[data-rm]'); if (r) { plans.splice(+r.dataset.rm, 1); drawPlans(); } });
  $('#addPlan', root).onclick = () => { plans.push({ amount: 100, validity_value: 30, validity_unit: 'days', is_best: false }); drawPlans(); };
  const edits = () => ({ name: $('#f_name', root).value, category_id: $('#f_cat', root).value, description: $('#f_desc', root).value, features: $('#f_feat', root).value, note: $('#f_note', root).value, plans,
    ...Object.fromEntries($$('[data-id]', root).map((i) => [i.dataset.id, i.value.trim()]).filter(([, v]) => v !== '' || true)) });
  $('#save', root).onclick = async () => { try { await api(`/submissions/${subId}`, { method: 'PATCH', body: edits() }); toast('Saved ✨'); } catch (e) { toast(e.message, 'err'); } };

  // ------------------------------------------------ live checks
  const live = $('#live', root);
  let hours = 24;
  function drawLive(s) {
    d = { ...d, ...s };
    if (!pending) { live.innerHTML = `<h3>🧪 Live checks</h3><p class="muted">This submission was ${d.status}.</p>`; return; }
    const need = d.checks.filter((c) => c.required && !c.passed).map((c) => c.label);
    live.innerHTML = `<h3>🧪 Live checks <span class="badge ${d.ready ? 'ok' : 'warn'}">${d.checks.filter((c) => c.required && c.passed).length}/${d.checks.filter((c) => c.required).length}</span></h3>
      <p class="muted tiny">Install the APK, log in with a <b>test key</b> and use the app. Each real API call ticks a check here automatically.</p>
      <dl class="kv"><dt>Endpoint</dt><dd class="mono">${esc(d.endpoint)}</dd><dt>API key</dt><dd class="mono">${esc(d.api_key)}</dd></dl>
      <div class="row"><button class="btn sm" data-copy-text="${esc(d.endpoint)}">Copy endpoint</button><button class="btn sm" data-copy-text="${esc(d.api_key)}">Copy API key</button></div>
      <div class="row"><input id="hrs" type="number" min="1" max="720" value="${hours}" style="width:90px" aria-label="Hours"><span class="muted">hours</span><button class="btn lime w-only" id="mkkey">🔑 Generate test key</button></div>
      <div class="list">${d.test_keys.length ? d.test_keys.map((k) => `<div class="li wide"><div class="avatar">🔑</div><div class="meta"><b class="mono" data-copy-text="${esc(k.key)}">${esc(k.key)}</b><small>${k.status} · ${k.status === 'active' ? until(k.expires_at) : ''} · ${k.bound ? '🔒 ' + esc(k.device) : 'no device yet'}</small></div>
        <div class="acts"><button class="btn sm danger w-only" data-ka="revoke" data-k="${esc(k.key)}">⛔ Revoke</button><button class="btn sm w-only" data-ka="expire" data-k="${esc(k.key)}">⏱ Expire</button><button class="btn sm w-only" data-ka="reset-device" data-k="${esc(k.key)}">📲 Reset device</button><button class="btn sm w-only" data-ka="restore" data-k="${esc(k.key)}">↩️ Restore</button></div></div>`).join('') : '<p class="muted tiny">No test key yet — generate one.</p>'}</div>
      <div class="row between"><span>🛠 Test maintenance mode</span><button class="btn sm ${d.review_maintenance ? 'danger' : ''} w-only" id="maint">${d.review_maintenance ? 'Turn OFF' : 'Turn ON'}</button></div>
      <div class="stack">${d.checks.map((c) => `<div class="check ${c.passed ? 'pass' : 'waiting'}"><div class="ico">${c.passed ? '✓' : '…'}</div><div><b>${esc(c.label)}</b><br><small class="muted">${c.passed ? '✅ ' + ({ login: 'logic check pass', device: 'device uniqueness check pass', maintenance: 'maintenance check pass', revoke: 'revoke check pass', expire: 'expiry check pass' }[c.key] || 'check pass') + ' · ' + fmtDate(c.ts) : esc(c.hint)}</small></div><span class="badge ${c.required ? 'violet' : ''}">${c.required ? 'required' : 'optional'}</span></div>`).join('')}</div>
      <div class="row between"><b>Recent API calls</b><button class="btn sm w-only" id="rstc">↺ Reset checks</button></div>
      <div class="events">${d.events.length ? d.events.map((ev) => `<div><span class="mono">${new Date(ev.ts * 1000).toLocaleTimeString('en-IN')}</span><b>${esc(ev.label)}</b><span class="muted">${esc(ev.device)}</span></div>`).join('') : '<span class="muted tiny">Waiting for the first call…</span>'}</div>`;
    const ap = $('#approve', root);
    if (ap) { ap.disabled = !d.ready; $('#why', root).textContent = d.ready ? 'All required checks passed — you can approve.' : `Still missing: ${need.join(', ')}.`; }
  }
  drawLive(d);
  live.addEventListener('input', (e) => { if (e.target.id === 'hrs') hours = e.target.value; });
  live.addEventListener('click', async (e) => {
    if (e.target.closest('[data-copy-text]')) return;
    try {
      if (e.target.closest('#mkkey')) { const r = await api(`/submissions/${subId}/test-key`, { method: 'POST', body: { hours } }); copyText(r.key); toast('Test key created and copied'); }
      else if (e.target.closest('#maint')) await api(`/submissions/${subId}/maintenance`, { method: 'POST', body: { enabled: !d.review_maintenance } });
      else if (e.target.closest('#rstc')) { if (!(await confirmBox('Reset checks?', 'All ticks and the call log are cleared.', 'Reset', false))) return; await api(`/submissions/${subId}/checks/reset`, { method: 'POST' }); }
      else { const b = e.target.closest('[data-ka]'); if (!b) return; await api(`/submissions/${subId}/test-keys/${b.dataset.k}/${b.dataset.ka}`, { method: 'POST' }); }
      refresh();
    } catch (er) { toast(er.message, 'err'); }
  });
  async function refresh() { try { drawLive(await api(`/submissions/${subId}`)); } catch { /* keep the last state */ } }
  if (pending) tick(() => { if (!document.hidden && !document.querySelector('.modal-back')) refresh(); }, 3000);

  const ap = $('#approve', root);
  if (ap) {
    ap.onclick = async () => { try { await api(`/submissions/${subId}/approve`, { method: 'POST', body: {} }); toast('Approved — the seller was notified 🎉'); location.hash = '#/seller'; } catch (e) { toast(e.message, 'err'); } };
    $('#reject', root).onclick = () => openModal({ title: 'Reject app', danger: true, submit: 'Reject', body: fld('Reason (sent to the seller)', '<textarea name="reason" required rows="3" maxlength="400"></textarea>'),
      onSubmit: async (v) => { await api(`/submissions/${subId}/reject`, { method: 'POST', body: v }); toast('Rejected'); location.hash = '#/seller'; } });
    if (isSuper()) { const f = document.createElement('button'); f.className = 'btn sm'; f.textContent = 'Approve without checks'; f.onclick = async () => { if (await confirmBox('Approve without the checks?', 'Only do this if you tested the app another way. It is recorded in the logs.', 'Force approve')) { try { await api(`/submissions/${subId}/approve`, { method: 'POST', body: { force: true } }); toast('Approved'); location.hash = '#/seller'; } catch (e) { toast(e.message, 'err'); } } }; $('#reject', root).before(f); }
  }
}
