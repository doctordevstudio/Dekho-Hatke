// Bot appearance: button colours, 1-or-2 column layouts, auto reactions, celebration GIFs and a few bot limits.
// Admin = main bot (/settings/botui), reseller = own bot (/botui). Same editor, different base.
import { $, $$, api, confirmBox, empty, esc, fld, skeleton, toast } from '../core.js';

const COLOR = { '': ['Default', '#8a8f98'], primary: ['Blue', '#2f7de1'], success: ['Green', '#2ea043'], danger: ['Red', '#e5484d'] };

export async function botuiEditor(box, base) {
  box.innerHTML = skeleton(3);
  let d;
  try { d = await api(base); } catch (e) { box.innerHTML = empty('💥', e.message); return; }
  const s = d.settings;

  const colorRow = (g) => `<div class="row between" style="gap:10px;flex-wrap:wrap"><span>${esc(g.label)}</span>
    <span class="row" data-grp="${esc(g.key)}">${d.styles.map((o) => `<button type="button" class="btn sm ${s.colors[g.key] === o.key ? 'primary' : ''}" data-c="${esc(o.key)}"><i style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${(COLOR[o.key] || COLOR[''])[1]};margin-right:6px"></i>${esc(o.label)}</button>`).join('')}</span></div>`;
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
    $$('button', b.parentElement).forEach((x) => x.classList.toggle('primary', x === b));
  });

  const val = () => {
    const colors = {}, layout = {}, events = {}, gev = {};
    $$('[data-grp]', box).forEach((r) => { const on = $('button.primary', r); colors[r.dataset.grp] = on ? on.dataset.c : ''; });
    $$('[data-lay]', box).forEach((r) => { const on = $('button.primary', r); layout[r.dataset.lay] = on ? +on.dataset.n : 1; });
    $$('[data-ev]', box).forEach((r) => {
      events[r.dataset.ev] = $('[data-r]', r).value;
      gev[r.dataset.ev] = { enabled: $('[data-gon]', r).checked, url: $('[data-gurl]', r).value.trim() };
    });
    return { colors, layout, reactions: { enabled: $('#ren', box).checked, events },
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
