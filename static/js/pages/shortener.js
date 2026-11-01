// Shortener: step-gate links on the root-domain blog pages. Create, all-shorturl history, full tracking.
import { $, $$, api, confirmBox, copyText, debounce, empty, esc, fld, fmtDate, fmtNum, openModal, pager, rangeBar, skeleton, tabsBar, tileHtml, toast, userCell } from '../core.js';

const KIND = { license: ['🔑', 'Generates license key'], redirect: ['↪️', 'Redirects to a link'] };
const dur = (s) => { s = Math.round(s || 0); return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`; };
const num = (v, d) => (v === undefined || v === null || v === '' ? d : v);
const bars = (root) => $$('[data-w]', root).forEach((el) => { el.style.width = Math.max(0, Math.min(100, +el.dataset.w)) + '%'; });

export default async function (root) {
  root.innerHTML = `<div class="page-head"><div><h1>🔗 Shortener</h1><p>Create step-gate short links on your blog pages. Each page makes the visitor wait at the top and at the bottom; after the last <b>Generate</b> click they get a license key in the bot, or are redirected to a link.</p></div>
    <button class="btn w-only" id="cfg">⚙️ Pages &amp; website</button></div>
    <div id="warn"></div><div id="tabs"></div><div id="body">${skeleton(3)}</div>`;
  let d = { items: [], config: {} }, sel = '', tab = 'history';
  const tb = tabsBar([['history', '📜 All shorturl history'], ['create', '➕ Create shorturl']], 'history', (k) => { tab = k; sel = ''; paint(); });
  $('#tabs', root).append(tb.el);

  async function load() {
    d = await api('/shorteners');
    warn();
    paint();
  }
  function warn() {
    const c = d.config || {}, w = [];
    if (!c.proxy_ready) w.push('⚠️ <b>PROXY_SECRET</b> is not set on the server: the gate API stays closed and no link can work. Add it in Render → Environment (same value as in your Cloudflare worker).');
    if (!(c.pages || []).length) w.push('⚠️ No blog pages yet. Open <b>⚙️ Pages &amp; website</b> and add the pages that load <code>gate.js</code> (or give every shortener its own pages).');
    $('#warn', root).innerHTML = w.map((t) => `<div class="alert">${t}</div>`).join('');
  }
  const paint = () => (tab === 'create' ? paintCreate() : sel ? paintDetail() : paintList());

  // ---------------------------------------------------------------- list (history)
  function card(s) {
    const [ic, kl] = KIND[s.kind] || ['🔗', s.kind], c = s.counts || {};
    return `<div class="card sl-card ${s.active ? '' : 'dim'}" data-id="${esc(s.id)}">
      <div class="row between"><div class="grow"><b class="sl-name">${ic} ${esc(s.name)}</b><br><small class="muted">${kl} · ${s.pages} page${s.pages === 1 ? '' : 's'} · ⏱ top ${s.top_min === s.top_max ? s.top_min : s.top_min + '–' + s.top_max}s · bottom ${s.bottom_min === s.bottom_max ? s.bottom_min : s.bottom_min + '–' + s.bottom_max}s</small></div>
        <label class="row tiny muted w-only" title="Switch this shortener on / off"><span class="switch"><input type="checkbox" data-on="${esc(s.id)}" ${s.active ? 'checked' : ''}><i></i></span></label></div>
      <div class="sl-counts gap-top">
        <div><span class="lbl">Today</span><b>${fmtNum(c.today)}</b></div><div><span class="lbl">Yesterday</span><b>${fmtNum(c.yesterday)}</b></div>
        <div><span class="lbl">All time</span><b>${fmtNum(c.all)}</b></div><div><span class="lbl">In progress</span><b>${fmtNum(c.active_now)}</b></div></div>
      <div class="tiny muted">successful completions${s.plans ? ` · used by ${s.plans} plan${s.plans === 1 ? '' : 's'}` : ''}${s.kind === 'redirect' && s.redirect_url ? ` · → ${esc(s.redirect_url)}` : ''}</div>
      ${s.link ? `<div class="row gap-top"><input class="grow mono" readonly value="${esc(s.link)}" aria-label="Short link"><button class="btn sm" data-copy="${esc(s.link)}">📋 Copy link</button></div>` : ''}
      <div class="row gap-top"><button class="btn sm primary" data-open="${esc(s.id)}">📊 Tracking</button><button class="btn sm w-only" data-edit="${esc(s.id)}">✏️ Edit</button><button class="btn sm danger w-only del-only" data-del="${esc(s.id)}">🗑</button></div></div>`;
  }
  function paintList() {
    $('#body', root).innerHTML = d.items.length ? `<div class="grid auto sl-grid">${d.items.map(card).join('')}</div>`
      : empty('🔗', 'No shorturl yet. Open the “Create shorturl” tab to make the first one.');
  }

  // ---------------------------------------------------------------- create / edit form
  function formHtml(v = {}, edit = false) {
    const k = v.kind || 'license';
    return `${fld('Shortener name * <span class="muted">(only you see it in this panel)</span>', `<input name="name" required maxlength="60" placeholder="e.g. Free key – 3 pages" value="${esc(v.name || '')}">`)}
      ${fld('After the last “Generate” click *', `<select name="kind" ${edit ? 'disabled' : ''}><option value="license" ${k === 'license' ? 'selected' : ''}>🔑 Generate license key (sent in the Telegram bot)</option><option value="redirect" ${k === 'redirect' ? 'selected' : ''}>↪️ Redirect to a link (no Telegram message)</option></select>`, edit ? 'The type cannot be changed after creation.' : 'License key shortener can be chosen in Plans (shown as FREE in the bot). Redirect shortener gives you a link to share.')}
      <div id="redirBox">${fld('Redirect URL *', `<input name="redirect_url" type="url" maxlength="500" placeholder="https://…" value="${esc(v.redirect_url || '')}">`, 'Where the visitor lands after the last Generate click.')}</div>
      <div class="two">${fld('Total number of pages *', `<input name="pages" type="number" min="1" max="15" step="1" required value="${num(v.pages, 3)}">`, '1 – 15. A different blog page is used for every step.')}
        ${fld('Link valid for (minutes) *', `<input name="ttl_min" type="number" min="5" max="1440" step="1" required value="${num(v.ttl_min, 45)}">`, 'The whole run must finish in this time, otherwise the user left.')}</div>
      <div class="two">${fld('Top waiting — min (seconds) *', `<input name="top_min" type="number" min="0" max="600" required value="${num(v.top_min, 15)}">`, 'Wait at the TOP of every page before “Continue”.')}
        ${fld('Top waiting — max (seconds) *', `<input name="top_max" type="number" min="0" max="600" required value="${num(v.top_max, num(v.top_min, 25))}">`, 'Same as min = fixed. Otherwise random per page.')}</div>
      <div class="two">${fld('Bottom waiting — min (seconds) *', `<input name="bottom_min" type="number" min="0" max="600" required value="${num(v.bottom_min, 10)}">`, 'Wait at the BOTTOM of every page before “Verify / Generate”.')}
        ${fld('Bottom waiting — max (seconds) *', `<input name="bottom_max" type="number" min="0" max="600" required value="${num(v.bottom_max, num(v.bottom_min, 20))}">`)}</div>
      <div class="seller-note" id="est"></div>
      <div id="licBox"><div class="two gap-top">${fld('Open links per user', `<input name="max_active" type="number" min="1" max="20" value="${num(v.max_active, 3)}">`, 'How many unfinished links one user may hold.')}
        ${fld('Seconds between two links', `<input name="cooldown" type="number" min="0" max="3600" value="${num(v.cooldown, 5)}">`)}</div>
        ${fld('Keys per user per day', `<input name="daily_limit" type="number" min="0" max="500" value="${num(v.daily_limit, 0)}">`, '0 = unlimited.')}
        <label class="row gap-top"><span class="switch"><input type="checkbox" name="notify_pages" ${v.notify_pages !== false ? 'checked' : ''}><i></i></span> 🔔 Tell the user in the bot after every page (“Page 1 completed, 2 remaining”)</label></div>
      ${fld('Blog pages for this shortener <span class="muted">(optional)</span>', `<textarea name="pool" rows="4" placeholder="/blog/post-one/&#10;/blog/post-two/&#10;&#8203;(leave empty to use the shared pool from ⚙️ Pages &amp; website)">${esc((v.pool || []).join('\n'))}</textarea>`, 'One path per line. Every page must load gate.js.')}
      ${fld('Note (admin only)', `<input name="note" maxlength="200" value="${esc(v.note || '')}">`)}
      <label class="row gap-top"><span class="switch"><input type="checkbox" name="active" ${v.active !== false ? 'checked' : ''}><i></i></span> Active</label>`;
  }
  function wire(box) {
    const f = $('form', box) || box, g = (n) => f.elements[n];
    const sync = () => {
      const lic = g('kind').value === 'license';
      $('#licBox', box).hidden = !lic; $('#redirBox', box).hidden = lic;
      const n = +g('pages').value || 0, a = (+g('top_min').value || 0) + (+g('bottom_min').value || 0), b = (+g('top_max').value || 0) + (+g('bottom_max').value || 0);
      $('#est', box).innerHTML = `⏱ A visitor needs at least <b>${dur(n * a)}</b>${b !== a ? ` (up to <b>${dur(n * b)}</b>)` : ''} of waiting to finish all <b>${n}</b> page${n === 1 ? '' : 's'}.`;
    };
    f.addEventListener('input', sync); f.addEventListener('change', sync); sync();
  }
  const read = (v, f) => ({ ...v, active: f.elements.active.checked, notify_pages: f.elements.notify_pages.checked, kind: f.elements.kind.value });

  function paintCreate() {
    $('#body', root).innerHTML = `<form class="card sl-form" id="cf" novalidate><h2>➕ Create shorturl</h2>${formHtml()}<div class="row gap-top"><button class="btn primary" type="submit">Create shorturl</button></div></form>`;
    const f = $('#cf', root); wire(f);
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = $('button[type=submit]', f); btn.disabled = true;
      try {
        const r = await api('/shorteners', { method: 'POST', body: read(Object.fromEntries(new FormData(f)), f) });
        toast('Shortener created ✨'); tab = 'history'; tb.set('history'); await load();
        if (r.item && r.item.link) copyText(r.item.link);
      } catch (err) { toast(err.message, 'err'); } finally { btn.disabled = false; }
    });
  }
  function editForm(s) {
    const m = openModal({ title: 'Edit shorturl', wide: true, body: formHtml(s, true),
      onSubmit: async (v, f) => { await api(`/shorteners/${s.id}`, { method: 'PATCH', body: { ...read(v, f), kind: s.kind } }); toast('Saved ✨'); load(); } });
    wire(m.el);
  }
  function configForm() {
    const c = d.config || {};
    openModal({ title: '⚙️ Pages & website', wide: true, body: `${fld('Website address *', `<input name="site_url" type="url" required value="${esc(c.site_url || '')}">`, 'Your root domain. Links look like https://yourdomain/blog/post/?t=CODE — this app only builds the link, it never serves that domain.')}
      ${fld('Blog pages (shared pool)', `<textarea name="pages" rows="10" placeholder="/blog/automate-daily-tasks-python/&#10;/blog/flutter-setup-first-app/">${esc((c.pages || []).join('\n'))}</textarea>`, 'One path per line. Each step uses a different page from this pool. Every page must load /assets/gate.js.')}
      <div class="seller-note">${c.proxy_ready ? '✅ PROXY_SECRET is set.' : '⚠️ PROXY_SECRET is missing.'} ${c.turnstile ? '🛡 Cloudflare Turnstile is on.' : 'Turnstile is off (optional: TURNSTILE_SITEKEY / TURNSTILE_SECRET).'}</div>`,
      onSubmit: async (v) => { await api('/shorteners/config', { method: 'PUT', body: v }); toast('Saved ✨'); load(); } });
  }

  // ---------------------------------------------------------------- detail / tracking
  let range = { range: 'today', start: '', end: '' }, status = '', q = '', page = 1;
  function paintDetail() {
    const s = d.items.find((x) => x.id === sel);
    if (!s) { sel = ''; return paintList(); }
    const [ic, kl] = KIND[s.kind] || ['🔗', s.kind];
    $('#body', root).innerHTML = `<div class="row between"><button class="btn" id="back">← All shorturls</button><div class="tiny muted">${ic} ${kl} · created ${fmtDate(s.created_at)}${s.created_by ? ' by ' + esc(s.created_by) : ''}</div></div>
      <div class="section-title"><h2>${ic} ${esc(s.name)}</h2></div><div id="rb"></div>
      <div id="st" class="gap-top">${skeleton(2)}</div>
      <div class="section-title"><h2>🕵️ Every link (newest first)</h2></div>
      <div class="row"><div id="stab"></div><input id="sq" class="grow" type="search" placeholder="🔎 Search chat id, name, @username, key…"></div>
      <div id="sl" class="list gap-top"></div><div id="pg"></div>`;
    const rb = rangeBar((r) => { range = r; stats(); }, { initial: range.range });
    $('#rb', root).append(rb.el);
    const stab = tabsBar([['', 'All'], ['active', '⏳ In progress'], ['done', '✅ Completed'], ['expired', '🚪 Left']], status, (k) => { status = k; page = 1; sessions(); });
    $('#stab', root).append(stab.el);
    $('#sq', root).oninput = debounce((e) => { q = e.target.value.trim(); page = 1; sessions(); }, 350);
    stats(); sessions();
  }
  async function stats() {
    const box = $('#st', root); if (!box) return;
    try {
      const r = await api(`/shorteners/${sel}/stats`, { params: { range: range.range, start: range.start, end: range.end } });
      const x = r.stats, s = r.item, last = s.pages;
      const maxv = Math.max(1, x.started, ...x.funnel.map((f) => f.completed));
      box.innerHTML = `<div class="grid auto">${tileHtml('✅ Successful', fmtNum(x.done), 'completed every page', { hot: true })}${tileHtml('▶️ Links started', fmtNum(x.started), s.kind === 'license' ? 'created in the bot' : 'visitors who opened the short link')}
        ${tileHtml('👀 Opened page 1', fmtNum(x.opened), 'really visited the site')}${tileHtml('🚪 Left without finishing', fmtNum(x.left_total), 'link expired unfinished')}
        ${tileHtml('📈 Success rate', x.rate + '%', 'successful ÷ started')}${tileHtml('⏳ In progress now', fmtNum(x.active_now), Object.entries(x.active_by_page || {}).map(([p, n]) => `${n} on page ${+p + 1}`).join(' · ') || 'nobody right now')}</div>
      <div class="card gap-top"><h3>Where do users stop?</h3><p class="tiny muted">“Left after page X” = finished page X, then never came back before the link expired. Counted on the day they were last active.</p>
        <div class="funnel">
          <div class="frow"><span class="fl">Started</span><div class="bar"><i data-w="100"></i></div><b>${fmtNum(x.started)}</b></div>
          <div class="frow left"><span class="fl">Left before finishing page 1</span><div class="bar"><i data-w="${x.started ? (x.left_before_page1 / x.started) * 100 : 0}"></i></div><b>${fmtNum(x.left_before_page1)}</b></div>
          ${x.funnel.map((f) => `<div class="frow"><span class="fl">Completed page ${f.page}${f.page === last ? ' (Generate)' : ''}</span><div class="bar"><i data-w="${(f.completed / maxv) * 100}"></i></div><b>${fmtNum(f.completed)}</b></div>
            ${f.page < last ? `<div class="frow left"><span class="fl">Left after completing page ${f.page}</span><div class="bar"><i data-w="${x.started ? (f.left_after / x.started) * 100 : 0}"></i></div><b>${fmtNum(f.left_after)}</b></div>` : ''}`).join('')}
        </div></div>`;
      bars(box);
    } catch (e) { box.innerHTML = empty('💥', e.message); }
  }
  async function sessions() {
    const list = $('#sl', root); if (!list) return;
    list.innerHTML = skeleton(3);
    try {
      const r = await api(`/shorteners/${sel}/sessions`, { params: { status, page, q } });
      list.innerHTML = r.items.length ? r.items.map(row).join('') : empty('🫥', 'No links match.');
      pager($('#pg', root), r, (p) => { page = p; sessions(); });
    } catch (e) { list.innerHTML = empty('💥', e.message); }
  }
  const stBadge = (x) => x.status === 'done' ? (x.kind === 'license' && !x.delivered ? '<span class="badge warn">✅ done · key pending</span>' : '<span class="badge ok">✅ completed</span>')
    : x.status === 'active' ? '<span class="badge info">⏳ in progress</span>' : '<span class="badge bad">🚪 left</span>';
  function row(x) {
    const who = x.kind === 'license' ? `<div class="row">${userCell(x.user)}</div>` : '<div class="meta"><b>🌐 Visitor</b><small>redirect link · #' + esc(x.id) + '</small></div>';
    const left = x.status === 'expired' ? ` · left after page ${x.completed_pages}` : '';
    return `<details class="li wide sl-row"><summary class="row between">${who}<div class="right">${stBadge(x)}<br><small class="muted">${x.completed_pages}/${x.total} pages${left}</small></div></summary>
      <div class="sl-more">
        <div class="tiny muted">🆔 #${esc(x.id)} · created ${fmtDate(x.created)}${x.opened ? ' · opened ' + fmtDate(x.opened) : ' · never opened'} · last activity ${fmtDate(x.last)}${x.done_at ? ' · finished ' + fmtDate(x.done_at) : ''} · time on pages ${dur(x.spent)}${x.flags ? ` · <span class="badge bad">🚩 ${x.flags} bot flag${x.flags === 1 ? '' : 's'}</span>` : ''}</div>
        ${x.license_key ? `<div class="gap-top">🔑 <code>${esc(x.license_key)}</code> ${x.delivered ? '<span class="badge ok">sent in bot</span>' : ''}</div>` : ''}
        ${x.kind === 'license' && x.status === 'done' && !x.delivered ? `<div class="gap-top tiny">⚠️ The key is not delivered yet (${x.tries} attempt${x.tries === 1 ? '' : 's'}); it retries automatically.${x.error ? ' ' + esc(x.error) : ''}</div>` : ''}
        ${x.redirect ? `<div class="gap-top tiny">↪️ Redirected to <span class="mono">${esc(x.redirect)}</span></div>` : ''}
        <ol class="sl-pages">${x.pages.map((p, i) => `<li class="${p.done_at ? 'ok' : i === x.completed_pages && x.status === 'active' ? 'cur' : ''}"><span class="mono">${esc(p.path)}</span> <small class="muted">top ${p.top}s · bottom ${p.bottom}s${p.done_at ? ' · ✅ ' + fmtDate(p.done_at) : ''}</small></li>`).join('')}</ol></div></details>`;
  }

  // ---------------------------------------------------------------- events
  root.addEventListener('change', async (e) => {
    const t = e.target.closest('[data-on]'); if (!t) return;
    try { await api(`/shorteners/${t.dataset.on}/toggle`, { method: 'POST', body: { active: t.checked } }); toast(t.checked ? 'Shortener on ✅' : 'Shortener off 🙈'); load(); }
    catch (er) { toast(er.message, 'err'); t.checked = !t.checked; }
  });
  root.addEventListener('click', async (e) => {
    const cp = e.target.closest('[data-copy]'); if (cp) return copyText(cp.dataset.copy);
    const op = e.target.closest('[data-open]'); if (op) { sel = op.dataset.open; range = { range: 'today', start: '', end: '' }; status = ''; q = ''; page = 1; return paint(); }
    if (e.target.closest('#back')) { sel = ''; return paint(); }
    const ed = e.target.closest('[data-edit]'); if (ed) return editForm(d.items.find((x) => x.id === ed.dataset.edit));
    const del = e.target.closest('[data-del]');
    if (del) {
      if (await confirmBox('Delete shorturl?', 'Links already given out stop working. Its history is kept. Plans using it must be changed first.', 'Delete')) {
        try { await api(`/shorteners/${del.dataset.del}`, { method: 'DELETE' }); toast('Deleted'); load(); } catch (er) { toast(er.message, 'err'); }
      }
      return;
    }
    if (e.target.closest('.sl-card') && !e.target.closest('button,input,label,a')) { const c = e.target.closest('.sl-card'); sel = c.dataset.id; range = { range: 'today', start: '', end: '' }; status = ''; q = ''; page = 1; paint(); }
  });
  $('#cfg', root).onclick = configForm;
  load();
}
