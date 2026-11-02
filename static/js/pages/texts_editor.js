// Bot messages: every customer-facing message of the bot, reworded from the panel (admin = main bot, reseller = own bot).
import { $, $$, api, confirmBox, empty, esc, skeleton, toast } from '../core.js';

export async function textsEditor(box, base) {
  box.innerHTML = skeleton(3);
  let d;
  try { d = await api(base); } catch (e) { box.innerHTML = empty('💥', e.message); return; }
  let q = '';
  box.innerHTML = `<div class="card stack"><h3>💬 Bot messages</h3>
    <p class="muted tiny">Reword any message the bot sends to customers. Words in <span class="mono">{curly braces}</span> are filled in automatically (price, app name, key…) — keep them where you want that information to appear.
    HTML allowed: <span class="mono">&lt;b&gt; &lt;i&gt; &lt;u&gt; &lt;code&gt; &lt;a href&gt; &lt;blockquote&gt;</span>. Saved texts go live immediately; <b>Reset</b> returns to the built-in wording.
    Every screen is here: shop, orders, payments, wallet, withdraw, check-in &amp; spin (animation text too), Refer &amp; Earn, My Keys, support, seller, and the admin-chat commands (<b>Admin chat commands</b>, main bot only).
    The free-trial summary is one message (<b>Order summary: free trial</b>). Plan button wording is under <b>Plan buttons</b>, e.g. <span class="mono">{credits} credits at {price}</span>.
    An edited message keeps your wording until you press <b>Reset</b>.</p>
    <input id="tq" type="search" placeholder="Search messages…"></div><div id="tl" class="gap-top"></div>`;
  const list = $('#tl', box);

  function draw() {
    const items = d.items.filter((i) => !q || (i.label + ' ' + i.key + ' ' + i.text + ' ' + i.default).toLowerCase().includes(q));
    list.innerHTML = d.groups.map((g) => {
      const rows = items.filter((i) => i.group === g);
      if (!rows.length) return '';
      return `<div class="section-title"><h2>${esc(g)}</h2></div>` + rows.map((i) => `<details class="card gap-top" data-k="${esc(i.key)}" ${q ? 'open' : ''}>
        <summary class="row between"><b>${esc(i.label)}</b><span class="badge ${i.custom ? 'ok' : ''}">${i.custom ? 'customized' : 'default'}</span></summary>
        <div class="stack gap-top"><p class="tiny muted">${i.placeholders ? 'Placeholders: <span class="mono">' + esc(i.placeholders) + '</span>' : 'No placeholders.'}</p>
        <textarea rows="${Math.min(14, Math.max(4, (i.text || i.default).split('\n').length + 1))}" maxlength="3500">${esc(i.text || i.default)}</textarea>
        <div class="row"><button class="btn primary w-only" data-save>Save</button><button class="btn w-only" data-reset ${i.custom ? '' : 'disabled'}>Reset to default</button></div></div></details>`).join('');
    }).join('') || empty('🔎', 'No message matches.');
  }
  draw();
  $('#tq', box).addEventListener('input', (e) => { q = e.target.value.trim().toLowerCase(); draw(); });
  list.addEventListener('click', async (e) => {
    const card = e.target.closest('[data-k]');
    if (!card) return;
    const key = card.dataset.k;
    if (e.target.closest('[data-save]')) {
      try { d = await api(`${base}/${key}`, { method: 'PUT', body: { text: $('textarea', card).value } }); toast('Saved ✨ live in the bot now'); const open = $$('details[open]', list).map((x) => x.dataset.k); draw(); open.forEach((k) => { const el = list.querySelector(`[data-k="${CSS.escape(k)}"]`); if (el) el.open = true; }); }
      catch (err) { toast(err.message, 'err'); }
    } else if (e.target.closest('[data-reset]')) {
      if (!await confirmBox('Reset this message?', 'It goes back to the built-in wording.', 'Reset', false)) return;
      try { d = await api(`${base}/${key}`, { method: 'DELETE' }); toast('Reset'); draw(); } catch (err) { toast(err.message, 'err'); }
    }
  });
}
