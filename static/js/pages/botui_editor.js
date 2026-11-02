// Bot appearance: button colours, 1-or-2 column layouts, auto reactions, celebration GIFs and a few bot limits.
// Admin = main bot (/settings/botui), reseller = own bot (/botui). Same editor, different base.
import { $, $$, api, confirmBox, empty, esc, fld, skeleton, toast } from '../core.js';

const COLOR = { '': ['Default', '#8a8f98'], primary: ['Blue', '#2f7de1'], success: ['Green', '#2ea043'], danger: ['Red', '#e5484d'] };

export async function botuiEditor(box, base) {
  box.innerHTML = skeleton(3);
  let d;
  try { d = await api(base); } catch (e) { box.innerHTML = empty('💥', e.message); return; }
  const s = d.settings;

  const isHex = (v) => /^#[0-9a-f]{6}$/i.test(v || '');
  const colorRow = (g) => {
    const cur = s.colors[g.key] || '';
    return `<div class="row between" style="gap:10px;flex-wrap:wrap" data-cg="${esc(g.key)}"><span>${esc(g.label)}</span>
    <span class="row" data-grp="${esc(g.key)}">${d.styles.map((o) => `<button type="button" class="btn sm ${cur === o.key ? 'primary' : ''}" data-c="${esc(o.key)}"><i style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${(COLOR[o.key] || COLOR[''])[1]};margin-right:6px"></i>${esc(o.label)}</button>`).join('')}
      <button type="button" class="btn sm ${isHex(cur) ? 'primary' : ''}" data-c="${isHex(cur) ? esc(cur) : ''}" data-custom><i style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${isHex(cur) ? esc(cur) : 'conic-gradient(red,yellow,lime,aqua,blue,magenta,red)'};margin-right:6px"></i>🎨 Custom</button></span></div>`;
  };
  const layoutRow = (l) => `<div class="row between"><span>${esc(l.label)}</span>
    <span class="row" data-lay="${esc(l.key)}"><button type="button" class="btn sm ${s.layout[l.key] === 1 ? 'primary' : ''}" data-n="1">▬ 1 per row</button><button type="button" class="btn sm ${s.layout[l.key] === 2 ? 'primary' : ''}" data-n="2">▪▪ 2 per row</button></span></div>`;
  const eventRow = (e) => {
    const g = s.gifs.events[e.key] || { enabled: false, url: '' };
    return `<div class="card gap-top" data-ev="${esc(e.key)}"><div class="row between"><b>${esc(e.label)}</b></div>
      <div class="two gap-top">${fld('Reaction on the user’s message', `<select data-r><option value="">— none —</option>${d.reactions.map((r) => `<option ${s.reactions.events[e.key] === r ? 'selected' : ''}>${r}</option>`).join('')}</select>`)}
      ${fld('GIF link or Telegram file id', `<input data-gurl value="${esc(g.url)}" placeholder="empty = built-in confetti GIF" maxlength="300">`)}</div>
      <label class="row"><span class="switch"><input type="checkbox" data-gon ${g.enabled ? 'checked' : ''}><i></i></span> Send a GIF for this</label></div>`;
  };

  box.innerHTML = `<div class="card stack"><h3>🎨 Button colours</h3>
      <p class="muted tiny">Each group of buttons can be Blue, Green or Red. Colours show in Telegram apps that support them (Bot API 9.4+); older apps simply show the normal grey button. Nothing else changes.</p>
      <p class="muted tiny"><b>🎨 Custom</b> opens a colour picker (like MS Paint): drag on the colour field or type a hex code. Telegram itself can only paint a button blue, green or red, so the bot applies the <b>closest of the three</b> and, with the switch below, puts a <b>dot in your colour family</b> (🟣 🟠 🟡 …) in front of the button text, which shows the same in every Telegram app.</p>
      <label class="row"><span class="switch"><input type="checkbox" id="dots" ${s.color_dots !== false ? 'checked' : ''}><i></i></span> Show a colour dot on custom-coloured buttons</label>
      <div class="stack" id="cg">${d.groups.map(colorRow).join('')}</div></div>
    <div class="card stack gap-top"><h3>▦ Button layout</h3><p class="muted tiny">Show a list as one wide button per row, or a grid of two. Back / Home buttons always stay full width.</p>
      <div class="stack" id="lg">${d.layouts.map(layoutRow).join('')}</div></div>
    <div class="card stack gap-top"><h3>😍 Auto reactions &amp; 🎞 GIFs</h3>
      <p class="muted tiny">The bot reacts to the user’s message after a purchase or a right action and can send a short celebration GIF that deletes itself. The spin-wheel animation is removed after the seconds below.</p>
      <label class="row"><span class="switch"><input type="checkbox" id="ren" ${s.reactions.enabled ? 'checked' : ''}><i></i></span> Reactions on</label>
      <label class="row"><span class="switch"><input type="checkbox" id="gen" ${s.gifs.enabled ? 'checked' : ''}><i></i></span> GIFs on</label>
      <div class="two">${fld('Remove GIF after (seconds)', `<input id="gdel" type="number" min="1" max="60" value="${esc(s.gifs.delete_after)}">`)}${fld('Remove spin wheel GIF after (seconds)', `<input id="sdel" type="number" min="1" max="60" value="${esc(s.gifs.spin_delete_after)}">`)}</div>
      <div id="evs">${d.events.map(eventRow).join('')}</div></div>
    <div class="card stack gap-top"><h3>⚙️ Bot limits</h3>
      <div class="two">${fld('FAQ questions per page', `<input id="fps" type="number" min="3" max="10" value="${esc(s.faq_page_size)}">`, '3 – 10')}
      ${fld('UPI payment window (minutes)', `<input id="uw" type="number" min="3" max="120" value="${esc(s.upi_window_min)}">`, 'Time the user has to pay an UPI order')}
      ${fld('Payment requests per user per day', `<input id="mtd" type="number" min="1" max="50" value="${esc(s.max_txn_per_day)}">`, 'Stops spam orders')}</div></div>
    <div class="row gap-top"><button class="btn primary w-only" id="sv">Save appearance</button><button class="btn w-only" id="rs">Reset to defaults</button></div>`;

  // pick-a-colour / pick-a-layout buttons just flip the "primary" highlight; values are read back on Save
  box.addEventListener('click', (e) => {
    const c = e.target.closest('[data-grp] [data-c]'), n = e.target.closest('[data-lay] [data-n]');
    const b = c || n;
    if (!b) return;
    if (b.hasAttribute('data-custom')) { paint(b); return; }
    $$('button', b.parentElement).forEach((x) => x.classList.toggle('primary', x === b));
    const cust = $('[data-custom]', b.parentElement);
    if (cust) { cust.dataset.c = ''; cust.firstElementChild.style.background = 'conic-gradient(red,yellow,lime,aqua,blue,magenta,red)'; }
  });

  // ---- paint-like colour picker: hue strip + saturation/brightness field + swatches + hex box + live preview
  const hsv2hex = (h, sat, v) => {
    const f = (n) => { const k = (n + h / 60) % 6; const x = v - v * sat * Math.max(0, Math.min(k, 4 - k, 1)); return Math.round(x * 255).toString(16).padStart(2, '0'); };
    return '#' + f(5) + f(3) + f(1);
  };
  const hex2hsv = (hex) => {
    const r = parseInt(hex.slice(1, 3), 16) / 255, g = parseInt(hex.slice(3, 5), 16) / 255, b = parseInt(hex.slice(5, 7), 16) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), dl = mx - mn;
    let h = 0;
    if (dl) h = mx === r ? ((g - b) / dl) % 6 : mx === g ? (b - r) / dl + 2 : (r - g) / dl + 4;
    return [((h * 60) + 360) % 360, mx ? dl / mx : 0, mx];
  };
  const SWATCHES = ['#e5484d', '#f76b15', '#ffb224', '#ffe629', '#46a758', '#12a594', '#00a2c7', '#2f7de1', '#3e63dd', '#8e4ec6', '#d6409f', '#ab6400', '#111111', '#6f6d78', '#c8c7cb', '#ffffff'];
  function paint(btn) {
    const row = btn.parentElement, now = isHex(btn.dataset.c) ? btn.dataset.c : '#8a2be2';
    let [h, sa, v] = hex2hsv(now);
    const wrap = document.createElement('div');
    wrap.className = 'paint-wrap';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:99;padding:16px';
    wrap.innerHTML = `<div class="card stack" style="max-width:340px;width:100%;touch-action:none">
      <h3>🎨 Pick a colour</h3>
      <div id="pf" style="position:relative;height:170px;border-radius:12px;cursor:crosshair;touch-action:none;overflow:hidden"><div id="pfd" style="position:absolute;width:18px;height:18px;margin:-9px 0 0 -9px;border:3px solid #fff;border-radius:50%;box-shadow:0 0 0 1px #0006;pointer-events:none"></div></div>
      <input id="ph" type="range" min="0" max="359" style="width:100%;background:linear-gradient(90deg,red,yellow,lime,aqua,blue,magenta,red);height:14px;border-radius:8px;-webkit-appearance:none;appearance:none">
      <div style="display:grid;grid-template-columns:repeat(8,1fr);gap:6px" id="psw">${SWATCHES.map((x) => `<button type="button" data-sw="${x}" title="${x}" style="aspect-ratio:1;border-radius:8px;border:1px solid #0003;background:${x};cursor:pointer"></button>`).join('')}</div>
      <div class="row" style="gap:8px"><input id="px" maxlength="7" style="flex:1;font-family:monospace" placeholder="#8a2be2"><input id="pc" type="color" style="width:46px;height:40px;padding:0;border:none;background:none" title="Your device's own colour picker"></div>
      <div class="tiny muted">Preview in Telegram:</div>
      <div style="display:flex;gap:8px"><div id="pv" style="flex:1;text-align:center;padding:10px;border-radius:10px;color:#fff;font-weight:600">🛒 Shop</div><div id="pn" class="tiny muted" style="flex:1"></div></div>
      <div class="row"><button type="button" class="btn primary" id="pok">Use this colour</button><button type="button" class="btn" id="pcancel">Cancel</button></div></div>`;
    document.body.appendChild(wrap);
    const q = (sel) => wrap.querySelector(sel);
    const NATIVE = { primary: ['Blue', '#2f7de1'], success: ['Green', '#2ea043'], danger: ['Red', '#e5484d'], '': ['Default', '#8a8f98'] };
    function refresh(fromHex) {
      const hex = hsv2hex(h, sa, v);
      q('#pf').style.background = `linear-gradient(to top,#000,transparent),linear-gradient(to right,#fff,${hsv2hex(h, 1, 1)})`;
      const dot = q('#pfd'); dot.style.left = (sa * 100) + '%'; dot.style.top = ((1 - v) * 100) + '%';
      q('#ph').value = Math.round(h);
      if (!fromHex) q('#px').value = hex;
      q('#pc').value = hex; q('#pv').style.background = hex;
      const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
      q('#pv').style.color = (r * 299 + g * 587 + b * 114) / 1000 > 165 ? '#111' : '#fff';
      const hh = h, hi = Math.max(r, g, b), lo = Math.min(r, g, b);
      let nat = '';
      if (hi - lo >= 40) { const hues = { primary: 215, success: 130, danger: 358 }; nat = Object.keys(hues).sort((a, c) => Math.min(Math.abs(hh - hues[a]), 360 - Math.abs(hh - hues[a])) - Math.min(Math.abs(hh - hues[c]), 360 - Math.abs(hh - hues[c])))[0]; }
      q('#pn').textContent = `Telegram paints it ${NATIVE[nat][0]}${(q('#psw') && box.querySelector('#dots') && box.querySelector('#dots').checked) ? ' + a colour dot' : ''}.`;
    }
    const fieldPick = (ev) => { const r = q('#pf').getBoundingClientRect(); sa = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)); v = 1 - Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)); refresh(); };
    let drag = false;
    q('#pf').addEventListener('pointerdown', (ev) => { drag = true; q('#pf').setPointerCapture(ev.pointerId); fieldPick(ev); });
    q('#pf').addEventListener('pointermove', (ev) => { if (drag) fieldPick(ev); });
    q('#pf').addEventListener('pointerup', () => { drag = false; });
    q('#ph').addEventListener('input', (ev) => { h = +ev.target.value; refresh(); });
    q('#psw').addEventListener('click', (ev) => { const sw = ev.target.closest('[data-sw]'); if (sw) { [h, sa, v] = hex2hsv(sw.dataset.sw); refresh(); } });
    q('#px').addEventListener('input', (ev) => { let t = ev.target.value.trim(); if (t && t[0] !== '#') t = '#' + t; if (isHex(t)) { [h, sa, v] = hex2hsv(t); refresh(true); } });
    q('#pc').addEventListener('input', (ev) => { [h, sa, v] = hex2hsv(ev.target.value); refresh(); });
    q('#pcancel').onclick = () => wrap.remove();
    wrap.addEventListener('click', (ev) => { if (ev.target === wrap) wrap.remove(); });
    q('#pok').onclick = () => {
      const hex = hsv2hex(h, sa, v);
      $$('button', row).forEach((x) => x.classList.toggle('primary', x === btn));
      btn.dataset.c = hex; btn.firstElementChild.style.background = hex;
      wrap.remove();
    };
    refresh();
  }

  const val = () => {
    const colors = {}, layout = {}, events = {}, gev = {};
    $$('[data-grp]', box).forEach((r) => { const on = $('button.primary', r); colors[r.dataset.grp] = on ? on.dataset.c : ''; });
    $$('[data-lay]', box).forEach((r) => { const on = $('button.primary', r); layout[r.dataset.lay] = on ? +on.dataset.n : 1; });
    $$('[data-ev]', box).forEach((r) => {
      events[r.dataset.ev] = $('[data-r]', r).value;
      gev[r.dataset.ev] = { enabled: $('[data-gon]', r).checked, url: $('[data-gurl]', r).value.trim() };
    });
    return { colors, color_dots: $('#dots', box).checked, layout, reactions: { enabled: $('#ren', box).checked, events },
      gifs: { enabled: $('#gen', box).checked, delete_after: $('#gdel', box).value, spin_delete_after: $('#sdel', box).value, events: gev },
      faq_page_size: $('#fps', box).value, upi_window_min: $('#uw', box).value, max_txn_per_day: $('#mtd', box).value };
  };

  $('#sv', box).onclick = async () => {
    try { await api(base, { method: 'PUT', body: val() }); toast('Saved ✨ live in the bot now'); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#rs', box).onclick = async () => {
    if (!await confirmBox('Reset bot appearance?', 'Colours, layouts, reactions, GIFs and limits go back to the defaults.', 'Reset', false)) return;
    try { await api(base, { method: 'DELETE' }); toast('Reset'); botuiEditor(box, base); } catch (e) { toast(e.message, 'err'); }
  };
}
