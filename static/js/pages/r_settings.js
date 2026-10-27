// Reseller settings: bot token / name, welcome message, force-join channels (counts), password.
import { $, api, confirmBox, empty, esc, fld, fmtNum, openModal, rangeBar, skeleton, tabsBar, toast } from '../core.js';

export default async function (root) {
  let tab = 'bot';
  root.innerHTML = '<div class="page-head"><div><h1>⚙️ Settings</h1><p>Your bot, force-join channels and password.</p></div></div><div id="tb"></div><div id="main" class="gap-top"></div>';
  $('#tb', root).appendChild(tabsBar([['bot', '🤖 Bot'], ['join', '📢 Force join'], ['pw', '🔑 Password']], tab, (k) => { tab = k; draw(); }).el);
  const box = () => $('#main', root);
  async function draw() {
    box().innerHTML = skeleton(2);
    try { await ({ bot, join, pw }[tab])(); } catch (e) { box().innerHTML = empty('💥', e.message); }
  }
  async function bot() {
    const s = await api('/settings');
    box().innerHTML = `<div class="card"><form id="f" class="stack"><div class="two">${fld('Bot token', `<input name="bot_token" autocomplete="off" placeholder="${esc(s.bot_token)} (leave blank to keep)">`, 'From @BotFather. We check it with Telegram.')}${fld('Bot name', `<input name="bot_name" maxlength="60" value="${esc(s.bot_name)}">`)}</div>
      <p class="muted tiny">Bot username: ${s.bot_username ? '@' + esc(s.bot_username) : '—'} · panel login: <b>${esc(s.login_username)}</b> · admin commission ${s.commission_pct}%</p>
      ${fld('Support @username', `<input name="support_username" maxlength="64" value="${esc(s.support_username)}">`)}${fld('Welcome message (text or HTML)', `<textarea name="welcome_message" rows="6" maxlength="1500">${esc(s.welcome_message)}</textarea>`, 'Placeholders: {first_name} {full_name}')}<button class="btn primary">Save</button></form></div>`;
    $('#f', root).onsubmit = async (e) => { e.preventDefault(); const b = Object.fromEntries(new FormData(e.target)); if (!b.bot_token) delete b.bot_token;
      try { const d = await api('/settings', { method: 'PUT', body: b }); toast('Saved ✨'); if (d.bot_username) draw(); } catch (er) { toast(er.message, 'err'); } };
  }
  async function join() {
    let rng = { range: 'all', start: '', end: '' };
    box().innerHTML = '<div class="row between"><p class="muted">Users must join these channels before using your bot. Counts include only successful joins.</p><div class="row"><div id="rb"></div><button class="btn primary" id="add">＋ Add channel</button></div></div><div id="cl" class="list gap-top"></div>';
    $('#rb', root).appendChild(rangeBar((r) => { rng = r; load(); }, { initial: 'all' }).el);
    async function load() {
      const d = await api('/channels', { params: rng });
      $('#cl', root).innerHTML = d.items.length ? d.items.map((c) => `<div class="li wide"><div class="avatar">📢</div><div class="meta"><b>${esc(c.title)}</b><small>${c.username ? '@' + esc(c.username) : esc(c.chat_id)}</small><div class="chips"><span class="badge ok">✅ ${fmtNum(c.joined)} joined</span><span class="badge">today ${fmtNum(c.today)}</span><span class="badge">yesterday ${fmtNum(c.yesterday)}</span><span class="badge">all ${fmtNum(c.all)}</span></div></div><div class="acts"><button class="btn sm danger" data-rm="${esc(c.id)}">🗑</button></div></div>`).join('') : empty('📢', 'No channels yet.');
    }
    $('#add', root).onclick = () => openModal({ title: 'Add channel', body: fld('Channel @username or ID', '<input name="chat_id" required maxlength="80" placeholder="@mychannel">', 'Add YOUR bot as an admin of the channel first.'), onSubmit: async (v) => { await api('/channels', { method: 'POST', body: v }); toast('Added ✨'); load(); } });
    box().onclick = async (e) => { const rm = e.target.closest('[data-rm]'); if (rm && await confirmBox('Remove channel?', 'Users no longer need to join it.', 'Remove')) { await api(`/channels/${rm.dataset.rm}`, { method: 'DELETE' }); load(); } };
    load();
  }
  function pw() {
    box().innerHTML = `<div class="card"><form id="f" class="stack">${fld('Current password', '<input name="current" type="password" required autocomplete="current-password">')}${fld('New password (10+ chars, letters + numbers)', '<input name="new" type="password" required minlength="10" autocomplete="new-password">')}<button class="btn primary">Change password</button></form></div>`;
    $('#f', root).onsubmit = async (e) => { e.preventDefault(); try { await api('/password', { method: 'POST', body: Object.fromEntries(new FormData(e.target)) }); toast('Password changed ✅'); e.target.reset(); } catch (er) { toast(er.message, 'err'); } };
  }
  draw();
}
