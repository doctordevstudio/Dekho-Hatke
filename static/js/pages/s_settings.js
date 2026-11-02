// Seller settings: Telegram connection, password, maintenance for all apps.
import { $, api, esc, fld, openModal, state, toast } from '../core.js';

export default async function (root) {
  const me = state.me;
  root.innerHTML = `<div class="page-head"><div><h1>⚙️ Settings</h1><p>Your account, Telegram alerts and password.</p></div></div>
    <div class="grid cols-2"><div class="card stack"><h3>📲 Telegram</h3><p class="muted">${me.telegram_linked ? '✅ Connected. You get sale, approval and withdrawal alerts in Telegram.' : 'Connect your Telegram to receive alerts and to submit apps for review.'}</p><div class="row"><button class="btn primary" id="tg">${me.telegram_linked ? '🔄 Reconnect' : '🔗 Connect Telegram'}</button></div></div>
      <div class="card stack"><h3>🔑 Change password</h3><form id="pf" class="stack">${fld('Current password', '<input name="current" type="password" required autocomplete="current-password">')}${fld('New password (10+ chars, letters + numbers)', '<input name="new" type="password" required minlength="10" autocomplete="new-password">')}<button class="btn primary">Change password</button></form><p class="tiny muted">Other devices are signed out.</p></div></div>
    <div class="card stack gap-top"><h3>🛠 Maintenance</h3><p class="muted tiny">Pause all your apps at once (or one app from <a href="#/apps">My Apps</a>). Your apps answer with your message while this is on.</p><div class="row"><button class="btn danger" id="m1">Turn ON for all apps</button><button class="btn" id="m0">Turn OFF</button></div></div>`;
  $('#tg', root).onclick = async () => { try { const d = await api('/telegram/link', { method: 'POST' }); if (!d.url) return toast('Bot is not available right now', 'err'); window.open(d.url, '_blank', 'noopener'); toast('Open Telegram and press START'); } catch (e) { toast(e.message, 'err'); } };
  $('#pf', root).onsubmit = async (e) => { e.preventDefault(); try { await api('/password', { method: 'POST', body: Object.fromEntries(new FormData(e.target)) }); toast('Password changed ✅'); e.target.reset(); } catch (er) { toast(er.message, 'err'); } };
  $('#m1', root).onclick = () => openModal({ title: 'Maintenance for all apps', submit: 'Turn on', body: fld('Message', '<input name="message" maxlength="300" value="We are upgrading — please try again soon.">'), onSubmit: async (v) => { await api('/maintenance', { method: 'POST', body: { app_id: 'all', enabled: true, message: v.message } }); toast('Maintenance ON'); } });
  $('#m0', root).onclick = async () => { try { await api('/maintenance', { method: 'POST', body: { app_id: 'all', enabled: false } }); toast('Maintenance off'); } catch (e) { toast(e.message, 'err'); } };
}
