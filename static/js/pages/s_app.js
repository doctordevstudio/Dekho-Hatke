// One of my apps: earnings today / yesterday / custom, plans sold and who bought (name only).
import { $, api, avatar, empty, esc, fmtDate, fmtMoney, fmtNum, pager, rangeBar, skeleton, tileHtml, toast } from '../core.js';

export default async function (root, appId) {
  let rng = { range: 'today', start: '', end: '' }, page = 1;
  root.innerHTML = skeleton(3);
  async function load() {
    try {
      const d = await api(`/apps/${appId}`, { params: { ...rng, page } }), a = d.app;
      root.innerHTML = `<div class="page-head"><div class="row">${avatar(a.image_url, a.name, 'lg')}<div><h1>${esc(a.name)}</h1><p><span class="badge ${{ live: 'ok', in_verification: 'warn', rejected: 'bad' }[a.status] || 'info'}">${esc(a.status_label)}</span> ${a.disabled ? '<span class="badge bad">disabled</span>' : ''}</p></div></div><div class="row"><a class="btn" href="#/apps">← Back</a><a class="btn primary" href="#/publish/${esc(a.id)}">✏️ Edit / update</a></div></div>
        ${a.reason ? `<div class="seller-note"><b>Rejected:</b> ${esc(a.reason)}</div>` : ''}${a.pending ? '<div class="seller-note">🕒 An update is under review. The live version keeps selling.</div>' : ''}
        <div class="grid cols-4 keep2">${tileHtml('Today', fmtMoney(a.ranges.today.earned), `${fmtNum(a.ranges.today.sales)} sales`, { hot: true })}${tileHtml('Yesterday', fmtMoney(a.ranges.yesterday.earned), `${fmtNum(a.ranges.yesterday.sales)} sales`)}${tileHtml('All time', fmtMoney(a.ranges.all.earned), `${fmtNum(a.ranges.all.sales)} sales`, { big: true })}
          <div class="tile custom"><div class="lbl">Custom range</div><div class="val">${a.ranges.custom ? fmtMoney(a.ranges.custom.earned) : '—'}</div><div class="ranges"><input type="date" id="cf"><input type="date" id="ct"><button class="btn sm primary" id="ca">Apply</button></div></div></div>
        ${a.live_plans ? `<div class="section-title"><h2>🏷️ Live plans</h2></div><div class="chips">${a.live_plans.map((p) => `<span class="badge violet">${esc(p.label)} · ${fmtMoney(p.amount)}${p.is_best ? ' ⭐' : ''}</span>`).join('')}</div>` : ''}
        <div class="section-title"><h2>🛒 Who bought</h2><div id="rb"></div></div><p class="tiny muted">${fmtNum(d.period_count)} sale${d.period_count === 1 ? '' : 's'} · ${fmtMoney(d.period_total)}</p>
        <div class="list">${d.items.length ? d.items.map((e) => `<div class="li wide"><div class="avatar">🙋</div><div class="meta"><b>${esc(e.buyer || '—')}</b><small>${esc(e.plan_label)} · ${fmtDate(e.ts)}</small></div><div class="acts"><b>${fmtMoney(e.amount)}</b></div></div>`).join('') : empty('🪙', 'No sales in this period.')}</div><div id="pg"></div>`;
      $('#rb', root).appendChild(rangeBar((x) => { rng = x; page = 1; load(); }, { initial: rng.range === 'all' ? 'today' : rng.range, withAll: false }).el);
      $('#ca', root).onclick = () => { const s = $('#cf', root).value, t = $('#ct', root).value; if (!s || !t) return toast('Pick both dates', 'err'); rng = { range: 'custom', start: s, end: t }; page = 1; load(); };
      pager($('#pg', root), d, (p) => { page = p; load(); });
    } catch (e) { root.innerHTML = empty('💥', e.message); }
  }
  load();
}
