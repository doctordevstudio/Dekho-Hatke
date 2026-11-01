// Bot screens: message + picture of every screen behind the bot's buttons (admin = main bot, reseller = own bot).
import { $, api, confirmBox, empty, esc, richEditor, skeleton, toast } from '../core.js';

const KIND = {
  welcome: 'Your message replaces the whole welcome text.',
  replace: 'Your message replaces the whole text of this screen.',
  intro: 'Your message is shown at the top; the live details (prices, balance, keys…) follow below it.',
};

export async function screensEditor(box, base) {
  box.innerHTML = skeleton(3);
  let items;
  try { items = (await api(base)).items; } catch (e) { box.innerHTML = empty('💥', e.message); return; }
  box.innerHTML = `<div class="card stack"><h3>🖼 Bot screens</h3>
    <p class="muted tiny">Change the <b>picture</b> and <b>message</b> of every screen behind the bot's buttons. Open a screen, edit, press <b>Save</b> — it is live immediately. <b>Reset</b> goes back to the built-in look.
    Telegram allows <b>1024 characters</b> under a picture: a screen whose text is longer is shown without its picture. Pictures: JPG / PNG / WEBP, up to 2 MB. The UPI payment QR is created per payment and cannot be replaced.</p></div>
    <div class="list gap-top" id="sc"></div>`;
  const list = $('#sc', box);
  list.innerHTML = items.map((s) => `<details class="card gap-top" data-k="${esc(s.key)}"><summary class="row between"><b>${esc(s.label)}</b><span class="badge ${s.image || (s.kind === 'intro' && s.text) ? 'ok' : ''}" data-st>${s.image || (s.kind === 'intro' && s.text) ? 'customized' : 'default'}</span></summary>
    <div class="stack gap-top"><p class="tiny muted">${esc(s.hint)} ${esc(KIND[s.kind])}${s.placeholders ? ` Placeholders: <span class="mono">${esc(s.placeholders)}</span>` : ''}${s.default_image ? ' Default picture: the Dekho Hatke logo (clear the picture box and save to keep it).' : ''}</p>
    <div data-ed></div><div class="row"><button class="btn primary" data-save>Save</button><button class="btn" data-reset>Reset to default</button></div></div></details>`).join('');
  items.forEach((s) => {
    const card = list.querySelector(`[data-k="${CSS.escape(s.key)}"]`);
    let ed = richEditor({ text: s.text, format: s.format, image: s.image, label: s.kind === 'intro' ? 'Message shown on top (optional)' : 'Message', rows: s.kind === 'intro' ? 4 : 7 });
    $('[data-ed]', card).appendChild(ed.el);
    $('[data-save]', card).onclick = async () => {
      try { await api(`${base}/${s.key}`, { method: 'PUT', body: ed.value() }); toast('Saved ✨'); const v = ed.value(); $('[data-st]', card).textContent = (v.image || (s.kind === 'intro' && v.text)) ? 'customized' : 'default'; $('[data-st]', card).classList.toggle('ok', !!(v.image || (s.kind === 'intro' && v.text))); }
      catch (e) { toast(e.message, 'err'); }
    };
    $('[data-reset]', card).onclick = async () => {
      if (!await confirmBox('Reset this screen?', 'The message and picture go back to the built-in look.', 'Reset', false)) return;
      try { await api(`${base}/${s.key}`, { method: 'DELETE' }); toast('Reset'); screensEditor(box, base); } catch (e) { toast(e.message, 'err'); }
    };
  });
}
