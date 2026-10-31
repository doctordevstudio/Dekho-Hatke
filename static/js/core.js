// Shared helpers: API client, toasts, modals, formatting, range tiles, pager.
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const PANEL = document.body.dataset.panel || 'admin';
const BASES = { admin: '/admin/api', reseller: '/reseller/api', seller: '/seller/api' };
// where to go when the session ends: the server tells us (on its own sub-domain the panel lives at '/')
const LOGINS = { admin: '/login', reseller: '/reseller', seller: '/seller' };
const loginUrl = () => document.body.dataset.login || LOGINS[PANEL];
export const state = { me: null, csrf: '', timers: [], panel: PANEL, base: BASES[PANEL] };

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

// ---------------------------------------------------------------- anti-replay request signing
// Every state-changing call carries a timestamp, a one-time nonce and an HMAC (key = the session's CSRF token) over
// method|path|ts|nonce. The server refuses stale timestamps, reused nonces and bad signatures, so a captured request can
// never be replayed.
const _enc = new TextEncoder();
const _hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
let _hmacKey = null, _hmacFor = '';
export async function signHeaders(method, url) {
  if (!(window.crypto && crypto.subtle)) return {};
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = _hex(crypto.getRandomValues(new Uint8Array(14)));
  const path = new URL(url, location.origin).pathname;
  if (!_hmacKey || _hmacFor !== state.csrf) {
    _hmacKey = await crypto.subtle.importKey('raw', _enc.encode(state.csrf), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    _hmacFor = state.csrf;
  }
  const sig = _hex(await crypto.subtle.sign('HMAC', _hmacKey, _enc.encode(`${method}|${path}|${ts}|${nonce}`)));
  return { 'X-Req-Ts': ts, 'X-Req-Nonce': nonce, 'X-Req-Sig': sig };
}

// ---------------------------------------------------------------- API
export async function api(path, { method = 'GET', body, params, form } = {}) {
  let url = state.base + path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v);
    const s = q.toString();
    if (s) url += '?' + s;
  }
  const opts = { method, credentials: 'same-origin', headers: { 'X-CSRF-Token': state.csrf } };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  if (method !== 'GET') Object.assign(opts.headers, await signHeaders(method, url));
  let res;
  try { res = await fetch(url, opts); } catch { throw new Error('Network error. Check your connection.'); }
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (res.status === 401) { location.href = loginUrl(); throw new Error('Session expired'); }
  if (!res.ok) throw new Error((data && (data.error || data.message)) || `Request failed (${res.status})`);
  return data;
}

// ---------------------------------------------------------------- formatting
const TZ = 'Asia/Kolkata';
export const fmtDate = (ts) => !ts ? '—' : new Date(ts * 1000).toLocaleString('en-IN', {
  timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
export const fmtMoney = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 });
export const fmtNum = (n) => Number(n || 0).toLocaleString('en-IN');
export function ago(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}
export function until(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return 'expired';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h left` : h ? `${h}h ${m}m left` : `${m}m left`;
}
export const mmss = (s) => { s = Math.max(0, Math.floor(s)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); };
export const today = () => new Date().toLocaleDateString('en-CA', { timeZone: TZ });
export const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

export function avatar(url, name, cls = '') {
  const letter = esc((name || '?').trim().charAt(0).toUpperCase() || '?');
  if (url) return `<img class="avatar ${cls}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-fallback="${letter}">`;
  return `<div class="avatar ${cls}">${letter}</div>`;
}
// <img> failures (dead URLs) fall back to the letter avatar. Capture phase because error events don't bubble.
document.addEventListener('error', (e) => {
  const t = e.target;
  if (t && t.tagName === 'IMG' && t.dataset.fallback !== undefined) {
    const d = document.createElement('div');
    d.className = t.className; d.textContent = t.dataset.fallback;
    t.replaceWith(d);
  }
}, true);

export function statusBadge(s, extra = '') {
  const map = { active: ['ok', '🟢 Active'], expired: ['bad', '🔴 Expired'], revoked: ['warn', '⛔ Revoked'],
    pending: ['warn', '⏳ Pending'], verification: ['info', '🕒 In verification'], success: ['ok', '✅ Success'],
    failed: ['bad', '❌ Failed'] };
  const [c, l] = map[s] || ['', esc(s)];
  return `<span class="badge ${c}">${l}${extra ? ' · ' + esc(extra) : ''}</span>`;
}

// ---------------------------------------------------------------- toast / modal
export function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast' + (type === 'err' ? ' err' : '');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), type === 'err' ? 6000 : 3200);
}
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied ✨'); }
  catch { toast('Copy failed — select and copy manually', 'err'); }
}

export function openModal({ title, body, submit = 'Save', cancel = 'Cancel', onSubmit, wide = false, danger = false, noFoot = false }) {
  const root = $('#modalRoot');
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal${wide ? ' wide' : ''}" role="dialog" aria-modal="true">
    <div class="head"><h2>${esc(title)}</h2><button class="icon-btn" data-x aria-label="Close">✕</button></div>
    <form novalidate>${body}${noFoot ? '' : `<div class="foot"><button type="button" class="btn" data-x>${esc(cancel)}</button>
    ${onSubmit ? `<button type="submit" class="btn ${danger ? 'danger' : 'primary'}">${esc(submit)}</button>` : ''}</div>`}</form></div>`;
  root.appendChild(back);
  const form = $('form', back);
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  back.addEventListener('click', (e) => { if (e.target.closest('[data-x]')) close(); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!onSubmit) return;
    const btn = $('button[type=submit]', form);
    btn.disabled = true;
    try { const r = await onSubmit(Object.fromEntries(new FormData(form)), form, close); if (r !== false) close(); }
    catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });
  const first = $('input:not([type=hidden]),select,textarea', form);
  if (first) setTimeout(() => first.focus(), 60);
  return { el: back, form, close };
}
export function confirmBox(title, text, okLabel = 'Confirm', danger = true) {
  return new Promise((resolve) => {
    let done = false;
    const m = openModal({ title, body: `<p class="muted">${text}</p>`, submit: okLabel, danger,
      onSubmit: async () => { done = true; resolve(true); } });
    new MutationObserver((_, o) => { if (!document.body.contains(m.el)) { if (!done) resolve(false); o.disconnect(); } })
      .observe($('#modalRoot'), { childList: true });
  });
}
export function lightbox(src) {
  openModal({ title: 'Screenshot', body: `<div class="lightbox"><img src="${esc(src)}" alt="screenshot"></div>`, noFoot: true, wide: true });
}

// ---------------------------------------------------------------- UI pieces
export function pager(el, meta, go) {
  if (!meta || meta.pages <= 1) { el.innerHTML = meta && meta.total ? `<span class="tiny">${fmtNum(meta.total)} total</span>` : ''; return; }
  el.className = 'pager';
  el.innerHTML = `<button class="btn sm" data-p="${meta.page - 1}" ${meta.page <= 1 ? 'disabled' : ''}>← Prev</button>
    <span>Page <b>${meta.page}</b> of ${meta.pages} · ${fmtNum(meta.total)} total</span>
    <button class="btn sm" data-p="${meta.page + 1}" ${meta.page >= meta.pages ? 'disabled' : ''}>Next →</button>`;
  el.onclick = (e) => { const b = e.target.closest('[data-p]'); if (b && !b.disabled) go(+b.dataset.p); };
}
export const skeleton = (n = 4) => '<div>' + '<div class="skel"></div>'.repeat(n) + '</div>';
export const empty = (icon, text) => `<div class="empty"><div class="big">${icon}</div>${esc(text)}</div>`;

export function userCell(u, size = '') {
  return `${avatar(u.profile_image_url, u.full_name, size)}<div class="meta"><b>${esc(u.full_name || '—')}</b>
    <small>${u.username ? '@' + esc(u.username) : 'no username'} · <span class="mono">${esc(u.chat_id)}</span></small></div>`;
}

/** Four stat tiles (today / yesterday / all time / custom) per block, with one shared custom date range.
 *  Every tile is clickable when the block has a `key` and `onDrill(key, range, {start, end})` is given. */
export function rangeBlocks(root, { fetch, build, after, onDrill }) {
  const custom = { start: '', end: '' };
  const tile = (key, r, label, value, sub, hot) => tileHtml(label, value, sub || '', { hot, drill: key && onDrill ? `${key}:${r}` : '' });
  function draw(data) {
    const blocks = build(data);
    root.innerHTML = blocks.map((b) => `<div class="section-title"><h2>${b.title}</h2></div>
      <div class="grid cols-4 keep2">
        ${tile(b.key, 'today', 'Today', b.fmt(b.data.today), b.sub?.today, true)}${tile(b.key, 'yesterday', 'Yesterday', b.fmt(b.data.yesterday), b.sub?.yesterday)}
        ${tile(b.key, 'all', 'All time', b.fmt(b.data.all), b.sub?.all)}
        <div class="tile custom"><div class="lbl">Custom range</div>
          <div class="val">${b.data.custom == null ? '—' : b.fmt(b.data.custom)}</div>
          <div class="ranges"><input type="date" data-from value="${esc(custom.start)}" aria-label="From"><input type="date" data-to value="${esc(custom.end)}" aria-label="To">
          <button class="btn sm primary" data-apply>Apply</button>
          ${b.key && onDrill && b.data.custom != null ? `<button class="btn sm" data-drill="${esc(b.key)}:custom">View all ›</button>` : ''}</div></div>
      </div>`).join('');
    if (after) after(data);
  }
  async function load() {
    try { draw(await fetch(custom.start, custom.end)); } catch (e) { toast(e.message, 'err'); }
  }
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-apply]');
    if (btn) {
      const t = btn.closest('.tile');
      custom.start = $('[data-from]', t).value; custom.end = $('[data-to]', t).value;
      if (!custom.start || !custom.end) return toast('Pick both dates first', 'err');
      return load();
    }
    const d = e.target.closest('[data-drill]');
    if (d && onDrill && d.dataset.drill.includes(':') && root.contains(d)) {
      const [key, r] = d.dataset.drill.split(':');
      if (r === 'custom' && (!custom.start || !custom.end)) return toast('Apply a custom range first', 'err');
      onDrill(key, r, { start: custom.start, end: custom.end });
    }
  });
  root.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.matches('[data-drill]')) e.target.click(); });
  return load();
}

export function tick(fn, ms = 1000) { const id = setInterval(fn, ms); state.timers.push(id); return id; }
export function clearTimers() { state.timers.forEach(clearInterval); state.timers = []; }

// ================================================================ v2 helpers
export const ROLE = () => (state.me && state.me.role) || '';
export const isSuper = () => ROLE() === 'admin';

/** multipart upload with a progress callback (fetch cannot report upload progress). */
export function upload(path, file, onProgress, extra = {}) {
  return signHeaders('POST', state.base + path).then((sig) => new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file);
    for (const [k, v] of Object.entries(extra)) fd.append(k, v);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', state.base + path);
    xhr.setRequestHeader('X-CSRF-Token', state.csrf);
    for (const [k, v] of Object.entries(sig)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100), e.loaded, e.total); };
    xhr.onerror = () => reject(new Error('Network error during upload.'));
    xhr.onload = () => {
      let d = null; try { d = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status === 401) { location.href = loginUrl(); return; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(d);
      else reject(new Error((d && (d.error || d.message)) || `Upload failed (${xhr.status})`));
    };
    xhr.send(fd);
  }));
}

export const fmtBytes = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';

export function setBar(el, pct) { const i = el.querySelector('i'); if (i) i.style.width = Math.max(0, Math.min(100, pct)) + '%'; }

/** Segmented control. opts = [[value, label], ...]. Returns {el, set(v)}. */
export function segmented(opts, value, onChange) {
  const el = document.createElement('div');
  el.className = 'seg';
  const draw = () => { el.innerHTML = opts.map(([v, l]) => `<button type="button" data-v="${esc(v)}" class="${v === value ? 'on' : ''}">${l}</button>`).join(''); };
  draw();
  el.addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (!b || b.dataset.v === value) return; value = b.dataset.v; draw(); onChange(value); });
  return { el, set(v) { value = v; draw(); }, get: () => value };
}

/** Tab bar. tabs = [[key, label, count?]] */
export function tabsBar(tabs, active, onChange) {
  const el = document.createElement('div');
  el.className = 'tabs';
  let cur = active;
  const draw = () => { el.innerHTML = tabs.map(([k, l, n]) => `<button type="button" data-k="${esc(k)}" class="${k === cur ? 'on' : ''}">${l}${n ? `<span class="count">${n}</span>` : ''}</button>`).join(''); };
  draw();
  el.addEventListener('click', (e) => { const b = e.target.closest('[data-k]'); if (!b || b.dataset.k === cur) return; cur = b.dataset.k; draw(); onChange(cur); });
  return { el, set(k) { cur = k; draw(); }, setTabs(t) { tabs = t; draw(); }, get: () => cur };
}

/** Today / Yesterday / All time / Custom picker. onChange({range,start,end}) */
export function rangeBar(onChange, { initial = 'today', withAll = true } = {}) {
  const el = document.createElement('div');
  el.className = 'row range-bar';
  const st = { range: initial, start: '', end: '' };
  const opts = [['today', 'Today'], ['yesterday', 'Yesterday']].concat(withAll ? [['all', 'All time']] : []).concat([['custom', 'Custom']]);
  const seg = segmented(opts, initial, (v) => { st.range = v; custom.hidden = v !== 'custom'; if (v !== 'custom') onChange({ ...st }); });
  const custom = document.createElement('div');
  custom.className = 'row'; custom.hidden = initial !== 'custom';
  custom.innerHTML = '<input type="date" data-from aria-label="From"><input type="date" data-to aria-label="To"><button type="button" class="btn sm primary" data-apply>Apply</button>';
  custom.addEventListener('click', (e) => {
    if (!e.target.closest('[data-apply]')) return;
    st.start = $('[data-from]', custom).value; st.end = $('[data-to]', custom).value;
    if (!st.start || !st.end) return toast('Pick both dates', 'err');
    onChange({ ...st });
  });
  el.append(seg.el, custom);
  return { el, state: st };
}

/** Modal with a paginated list. load(page) -> {items, page, pages, total, ...}; row(item) -> html. */
export function drill({ title, load, row, wide = true, header, onRow }) {
  const m = openModal({ title, wide, noFoot: true, body: '<div data-head></div><div class="list" data-list></div><div data-pg></div>' });
  const list = $('[data-list]', m.el), pg = $('[data-pg]', m.el), head = $('[data-head]', m.el);
  let items = [];
  async function go(p = 1) {
    list.innerHTML = skeleton(3);
    try {
      const d = await load(p);
      items = d.items;
      if (header) head.innerHTML = header(d);
      list.innerHTML = d.items.length ? d.items.map(row).join('') : empty('🫥', 'Nothing here yet.');
      pager(pg, d, go);
    } catch (e) { list.innerHTML = empty('💥', e.message); }
  }
  list.addEventListener('click', (e) => { const r = e.target.closest('[data-i]'); if (r && onRow) onRow(items[+r.dataset.i], m); });
  go(1);
  return m;
}

/** Stat tile (optionally clickable). */
export const tileHtml = (label, value, sub = '', { hot = false, big = false, drill: d = '' } = {}) =>
  `<div class="tile${hot ? ' hot' : ''}${big ? ' big-total' : ''}${d ? ' click' : ''}" ${d ? `data-drill="${esc(d)}" tabindex="0" role="button"` : ''}>
    <div class="lbl">${label}</div><div class="val">${value}</div><div class="sub">${sub}${d ? ' <span class="more">view all ›</span>' : ''}</div></div>`;

/** Run `fn(key)` when a [data-drill] tile inside `root` is clicked / Enter is pressed. */
export function bindDrill(root, fn) {
  root.addEventListener('click', (e) => { const t = e.target.closest('[data-drill]'); if (t) fn(t.dataset.drill, t); });
  root.addEventListener('keydown', (e) => { if (e.key === 'Enter') { const t = e.target.closest('[data-drill]'); if (t) fn(t.dataset.drill, t); } });
}

/** A form field helper used by the page modules. */
export const fld = (label, inner, hint = '') => `<label class="field"><span>${label}</span>${inner}${hint ? `<small class="muted">${hint}</small>` : ''}</label>`;

/** Rich message editor: HTML/text toggle, textarea, image URL + upload (admin / reseller panels). Returns {el, value()} */
export function richEditor({ text = '', format = 'html', image = '', label = 'Message', canUpload = true, hint = '', rows = 8 } = {}) {
  const el = document.createElement('div');
  el.className = 'rich';
  el.innerHTML = `<div class="row between"><span class="lbl-sm">${esc(label)}</span>
      <div class="seg"><button type="button" data-f="html" class="${format === 'html' ? 'on' : ''}">HTML</button><button type="button" data-f="text" class="${format === 'text' ? 'on' : ''}">Plain text</button></div></div>
    <textarea data-t rows="${rows}" maxlength="3500">${esc(text)}</textarea>
    <p class="tiny muted">${hint || 'HTML tags allowed: &lt;b&gt; &lt;i&gt; &lt;u&gt; &lt;code&gt; &lt;a href&gt; &lt;tg-spoiler&gt;'}</p>
    <div class="row"><input type="text" data-i class="grow" placeholder="Image URL or uploaded image (optional)" value="${esc(image)}" maxlength="400">
    ${canUpload ? '<label class="btn sm">📷 Upload<input type="file" data-u accept="image/png,image/jpeg,image/webp" hidden></label>' : ''}
    <button type="button" class="btn sm" data-clear>✕</button></div><div data-prev></div>`;
  let fmt = format;
  const prev = $('[data-prev]', el), inp = $('[data-i]', el);
  const showPrev = () => { prev.innerHTML = inp.value ? `<img class="shot big" src="${esc(inp.value)}" alt="">` : ''; };
  showPrev();
  el.addEventListener('click', (e) => {
    const b = e.target.closest('[data-f]');
    if (b) { fmt = b.dataset.f; $$('[data-f]', el).forEach((x) => x.classList.toggle('on', x === b)); }
    if (e.target.closest('[data-clear]')) { inp.value = ''; showPrev(); }
  });
  inp.addEventListener('change', showPrev);
  const up = $('[data-u]', el);
  if (up) up.addEventListener('change', async () => {
    if (!up.files[0]) return;
    try { const d = await upload('/upload/image', up.files[0]); inp.value = d.url; showPrev(); toast('Image uploaded ✨'); }
    catch (err) { toast(err.message, 'err'); }
    up.value = '';
  });
  return { el, value: () => ({ text: $('[data-t]', el).value, format: fmt, image: inp.value.trim() }) };
}

export const plural = (n, w) => `${fmtNum(n)} ${w}${n === 1 ? '' : 's'}`;
export const rangeLabel = (r) => ({ today: 'Today', yesterday: 'Yesterday', all: 'All time', custom: 'Custom range' }[r] || r);
export function userLine(u) { return `${esc(u.full_name || '—')}${u.username ? ' · @' + esc(u.username) : ''}`; }

// ================================================================ v3 helpers
// ---------------------------------------------------------------- custom dropdowns (no native <select> popup anywhere)
const CS = { open: null, pop: null };
function csClose() {
  if (CS.pop) CS.pop.remove();
  if (CS.open) CS.open.btn.setAttribute('aria-expanded', 'false');
  CS.pop = CS.open = null;
  document.removeEventListener('mousedown', csOutside, true);
  document.removeEventListener('keydown', csKey, true);
  window.removeEventListener('resize', csClose);
  window.removeEventListener('scroll', csScroll, true);
}
const csOutside = (e) => { if (CS.pop && !CS.pop.contains(e.target) && !(CS.open && CS.open.btn.contains(e.target))) csClose(); };
const csScroll = (e) => { if (CS.pop && !CS.pop.contains(e.target)) csClose(); };
function csKey(e) {
  if (!CS.pop) return;
  if (e.key === 'Escape') { e.stopPropagation(); const b = CS.open.btn; csClose(); b.focus(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = [...CS.pop.querySelectorAll('.cs-opt:not([disabled]):not([hidden])')];
    if (!items.length) return;
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
  }
}
function csLabel(sel) { const o = sel.options[sel.selectedIndex]; return o ? o.textContent : ''; }
function csOpen(sel, btn) {
  if (CS.open && CS.open.sel === sel) return csClose();
  csClose();
  const pop = document.createElement('div');
  pop.className = 'cs-pop'; pop.setAttribute('role', 'listbox');
  const many = sel.options.length > 9;
  pop.innerHTML = (many ? '<input class="cs-search" type="search" placeholder="Search…" autocomplete="off" aria-label="Search options">' : '') +
    '<div class="cs-list">' + [...sel.querySelectorAll('option, optgroup')].map((o) => o.tagName === 'OPTGROUP'
      ? `<div class="cs-group">${esc(o.label)}</div>`
      : `<button type="button" role="option" class="cs-opt${o.selected ? ' on' : ''}" data-v="${esc(o.value)}" aria-selected="${o.selected}" ${o.disabled ? 'disabled' : ''}>${esc(o.textContent)}</button>`).join('') + '</div>';
  document.body.appendChild(pop);
  const r = btn.getBoundingClientRect(), below = innerHeight - r.bottom, up = below < 240 && r.top > below;
  pop.style.minWidth = r.width + 'px';
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8)) + 'px';
  pop.style.maxHeight = Math.max(160, Math.min(320, (up ? r.top : below) - 16)) + 'px';
  pop.style.top = up ? Math.max(8, r.top - pop.offsetHeight - 6) + 'px' : r.bottom + 6 + 'px';
  CS.open = { sel, btn }; CS.pop = pop; btn.setAttribute('aria-expanded', 'true');
  pop.addEventListener('click', (e) => {
    const o = e.target.closest('.cs-opt'); if (!o || o.disabled) return;
    sel.value = o.dataset.v;
    sel.dispatchEvent(new Event('input', { bubbles: true })); sel.dispatchEvent(new Event('change', { bubbles: true }));
    csClose(); btn.focus();
  });
  const search = pop.querySelector('.cs-search');
  if (search) search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    pop.querySelectorAll('.cs-opt').forEach((o) => { o.hidden = !!q && !o.textContent.toLowerCase().includes(q); });
  });
  document.addEventListener('mousedown', csOutside, true);
  document.addEventListener('keydown', csKey, true);
  window.addEventListener('resize', csClose);
  window.addEventListener('scroll', csScroll, true);
  setTimeout(() => (search || pop.querySelector('.cs-opt.on') || pop.querySelector('.cs-opt'))?.focus({ preventScroll: false }), 0);
}
function csBuild(sel) {
  if (sel.dataset.cs || sel.multiple || sel.size > 1) return;
  sel.dataset.cs = '1';
  const wrap = document.createElement('div');
  wrap.className = 'cs';
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'cs-btn'; btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
  sel.before(wrap); wrap.append(btn, sel);
  sel.classList.add('cs-native'); sel.tabIndex = -1; sel.setAttribute('aria-hidden', 'true');
  const sync = () => {
    btn.innerHTML = `<span class="cs-val">${esc(csLabel(sel)) || '&nbsp;'}</span><i class="cs-caret" aria-hidden="true">▾</i>`;
    btn.disabled = sel.disabled; wrap.classList.toggle('disabled', sel.disabled);
  };
  sync();
  new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected'] });
  sel.addEventListener('change', sync);
  for (const prop of ['value', 'selectedIndex']) {   // programmatic assignments keep the label in step
    const d = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
    Object.defineProperty(sel, prop, { configurable: true, get() { return d.get.call(this); }, set(v) { d.set.call(this, v); sync(); } });
  }
  btn.addEventListener('click', () => csOpen(sel, btn));
  btn.addEventListener('keydown', (e) => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); csOpen(sel, btn); } });
}
export function initSelects(root = document) { root.querySelectorAll('select').forEach(csBuild); }
new MutationObserver((muts) => {
  for (const m of muts) for (const n of m.addedNodes) {
    if (n.nodeType !== 1) continue;
    if (n.tagName === 'SELECT') csBuild(n); else if (n.querySelector) n.querySelectorAll('select').forEach(csBuild);
  }
}).observe(document.documentElement, { childList: true, subtree: true });
initSelects();

// ---------------------------------------------------------------- file checks (name, size, type, extension)
export const FILE_RULES = {
  apk: { exts: ['apk'], label: '.apk file', mimes: ['application/vnd.android.package-archive', 'application/octet-stream', ''] },
  video: { exts: ['mp4'], label: '.mp4 video', mimes: ['video/mp4'] },
  image: { exts: ['png', 'jpg', 'jpeg', 'webp'], label: 'PNG / JPG / WEBP image', mimes: ['image/png', 'image/jpeg', 'image/webp'] },
};
export const fileExt = (name) => (String(name).split('.').pop() || '').toLowerCase();
/** Returns '' when the file is acceptable, otherwise a human reason. */
export function checkFile(file, kind, maxBytes) {
  const rule = FILE_RULES[kind];
  if (!file) return 'Choose a file.';
  if (rule) {
    if (!rule.exts.includes(fileExt(file.name))) return `${file.name}: only ${rule.label}s are allowed (got .${fileExt(file.name) || '?'}).`;
    if (file.type && !rule.mimes.includes(file.type)) return `${file.name}: wrong file type (${file.type}). Only ${rule.label}s are allowed.`;
  }
  if (!file.size) return `${file.name} is empty.`;
  if (maxBytes && file.size > maxBytes) return `${file.name} is ${fmtBytes(file.size)} — the limit is ${fmtBytes(maxBytes)}.`;
  return '';
}
/** Small "name · size · type" line shown after a file is picked / uploaded. */
export const fileLine = (name, size, type = '') =>
  `<span class="file-line"><b>${esc(name)}</b> · ${fmtBytes(size)}${type ? ' · <span class="mono">' + esc(type) + '</span>' : ''}</span>`;

/** Format a Telegram file id for display: only the super admin / admin / seller panels ever receive them. */
export const canSeeIds = () => state.panel === 'seller' || ['admin', 'admin2'].includes(ROLE());

/** Modal telling the reseller to contact support to pay the due. */
export function dueDialog(support) {
  const user = String(support || '').replace(/^@/, '');
  return openModal({ title: '⛔ Store paused — dues pending', noFoot: true,
    body: `<div class="due-dialog"><div class="big">🧾</div><p>Your earnings reached the limit. <b>Please contact customer support to pay your due</b> and reopen your store.</p>
      ${user ? `<a class="btn primary block" href="https://t.me/${esc(user)}" target="_blank" rel="noopener">💬 Contact support · @${esc(user)}</a>` : '<p class="muted">Support username is not set yet — ask the admin.</p>'}
      <button type="button" class="btn block" data-x>Close</button></div>` });
}
