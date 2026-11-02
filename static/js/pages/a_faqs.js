// FAQs (navigation drawer): the questions the bot shows (10 per page), plus who opened which question.
import { $, api, confirmBox, drill, empty, esc, fld, fmtDate, fmtNum, openModal, skeleton, tabsBar, toast } from '../core.js';

export default async function (root) {
  let tab = 'q';
  root.innerHTML = `<div class="page-head"><div><h1>❓ FAQs</h1><p>Questions shown in the bot under <b>❓ FAQ</b> — 10 per page. Tapping a question shows its answer.</p></div>
    <div><button class="btn primary w-only" id="add">＋ Add question</button></div></div><div id="tb"></div><div id="main" class="gap-top"></div>`;
  $('#tb', root).appendChild(tabsBar([['q', '📋 Questions'], ['v', '👁 Who viewed']], tab, (k) => { tab = k; draw(); }).el);
  const main = () => $('#main', root);

  function form(f = {}) {
    return `${fld('Question', `<input name="question" maxlength="200" required value="${esc(f.question || '')}" placeholder="How do I pay with UPI?">`)}
      ${fld('Answer', `<textarea name="answer" rows="8" maxlength="3000" required>${esc(f.answer || '')}</textarea>`, 'HTML allowed: &lt;b&gt; &lt;i&gt; &lt;u&gt; &lt;code&gt; &lt;a href&gt;')}
      <div class="two">${fld('Answer format', `<select name="format"><option value="html" ${f.format !== 'text' ? 'selected' : ''}>HTML</option><option value="text" ${f.format === 'text' ? 'selected' : ''}>Plain text</option></select>`)}
      ${fld('Order', `<input name="order" type="number" min="0" value="${esc(f.order || 0)}">`, '1 shows first, 0 = after the numbered ones')}</div>
      <label class="row"><span class="switch"><input type="checkbox" name="active" ${f.active !== false ? 'checked' : ''}><i></i></span> Visible in the bot</label>`;
  }
  const payload = (d) => ({ question: d.question, answer: d.answer, format: d.format, order: d.order, active: d.active === 'on' });

  async function draw() {
    main().innerHTML = skeleton(3);
    try { await (tab === 'q' ? questions() : viewers()); } catch (e) { main().innerHTML = empty('💥', e.message); }
  }

  async function questions() {
    const { items } = await api('/faqs');
    main().innerHTML = items.length ? `<div class="list">${items.map((f, i) => `<div class="li wide" data-i="${i}"><div class="avatar">❓</div>
      <div class="meta"><b>${esc(f.question)}</b><small>${esc(String(f.answer).replace(/<[^>]+>/g, '').slice(0, 110))}</small>
      <div class="chips"><span class="badge ${f.active ? 'ok' : ''}">${f.active ? 'visible' : 'hidden'}</span>${f.order ? `<span class="badge">#${f.order}</span>` : ''}<span class="badge violet" data-views="${esc(f.id)}">👁 ${fmtNum(f.views)} views</span></div></div>
      <div class="acts"><button class="btn sm w-only" data-ed="${i}">✏️</button><button class="btn sm danger del-only w-only" data-del="${i}">🗑</button></div></div>`).join('')}</div>`
      : empty('❓', 'No questions yet. Add the first one — the FAQ button in the bot appears once there is at least one.');
    main().onclick = async (e) => {
      const v = e.target.closest('[data-views]');
      if (v) return who(v.dataset.views, items.find((f) => f.id === v.dataset.views));
      const ed = e.target.closest('[data-ed]'), del = e.target.closest('[data-del]');
      if (ed) {
        const f = items[+ed.dataset.ed];
        openModal({ title: 'Edit question', wide: true, body: form(f), onSubmit: async (d) => { await api(`/faqs/${f.id}`, { method: 'PATCH', body: payload(d) }); toast('Saved ✨'); draw(); } });
      } else if (del) {
        const f = items[+del.dataset.del];
        if (!await confirmBox('Delete this question?', f.question)) return;
        try { await api(`/faqs/${f.id}`, { method: 'DELETE' }); toast('Deleted'); draw(); } catch (err) { toast(err.message, 'err'); }
      }
    };
  }

  const viewRow = (v) => `<div class="li wide"><div class="avatar">👁</div><div class="meta"><b>${esc(v.full_name || '—')}${v.username ? ' · @' + esc(v.username) : ''}</b>
    <small>${esc(v.question)}</small><small>${esc(v.chat_id)} · ${fmtDate(v.last_ts)}</small></div><div class="acts"><span class="badge">${fmtNum(v.count)}×</span></div></div>`;

  function who(id, f) {
    drill({ title: `Who opened “${(f && f.question || '').slice(0, 50)}”`, load: (p) => api('/faqs/views', { params: { faq: id, page: p } }), row: viewRow });
  }

  async function viewers() {
    const d = await api('/faqs/views');
    main().innerHTML = d.items.length ? `<p class="tiny muted">Every person who opened an answer, newest first.</p><div class="list">${d.items.map(viewRow).join('')}</div>${d.pages > 1 ? '<p class="tiny muted">Showing the latest page — open a question for its full list.</p>' : ''}`
      : empty('👁', 'Nobody has opened a question yet.');
  }

  $('#add', root).onclick = () => openModal({ title: 'New question', wide: true, body: form(), onSubmit: async (d) => { await api('/faqs', { method: 'POST', body: payload(d) }); toast('Added ✨'); draw(); } });
  draw();
}
