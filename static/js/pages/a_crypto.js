// Crypto (Monero) — SUPER ADMIN ONLY. The server refuses every /crypto call from the 2nd admin and view-only admins;
// this page also never renders for them.
import { $, api, avatar, empty, esc, fld, fmtDate, fmtMoney, fmtNum, isSuper, openModal, pager, rangeBar, skeleton, tabsBar, tileHtml, toast } from '../core.js';

const xmrFmt = (n) => Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 12 });
const usd = (n) => '$' + Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 2 });
const STATUS = { pending: ['⏳ waiting', 'warn'], verification: ['🔎 confirming', 'warn'], success: ['✅ paid', 'ok'], failed: ['✖ closed', ''] };

export default async function (root) {
  if (!isSuper()) { root.innerHTML = empty('🔒', 'Only the Super Admin can open Crypto.'); return; }
  let st, tab = 'home';
  root.innerHTML = `<div class="page-head"><div><h1>₿ Crypto</h1><p>Monero (XMR) wallet for bot payments — automatic, no approvals needed.</p></div></div><div id="tb"></div><div id="main" class="gap-top">${skeleton(3)}</div>`;
  const main = () => $('#main', root);
  const tabs = tabsBar([['home', '🏠 Wallet'], ['orders', '🧾 Crypto orders'], ['moves', '📤 Transfers'], ['set', '⚙️ Settings']], tab, (k) => { tab = k; draw(); });
  $('#tb', root).appendChild(tabs.el);

  async function draw() {
    main().innerHTML = skeleton(3);
    try {
      st = await api('/crypto/status');
      if (!st.configured && tab !== 'set') return setup();
      await ({ home, orders, moves, set: () => cryptoSettings(main(), st, draw) }[tab])();
    } catch (e) { main().innerHTML = empty('💥', e.message); }
  }

  // ---------------------------------------------------------------- first time: create the wallet
  function setup() {
    main().innerHTML = `<div class="card stack"><h3>👛 Create your Monero wallet</h3>
      <p class="muted">No wallet yet. One click creates a fresh wallet; its 25-word seed is <b>encrypted</b> with your password and stored in the database, so the wallet is restored automatically after every restart.</p>
      ${st.keypass_set ? '<span class="badge ok">✅ XMR_KEYPASS is set on the server</span>' : '<span class="badge warn">⚠ XMR_KEYPASS is not set</span><p class="tiny muted">Add an environment variable <span class="mono">XMR_KEYPASS</span> (Render → Environment) with the password you want to use, redeploy, then come back here and type the same password.</p>'}
      ${st.binary ? '' : '<p class="tiny muted">⚠ monero-wallet-rpc was not found on the server, so balances and transfers need it installed (see the deploy notes). Creating the wallet still works.</p>'}
      <button class="btn primary" id="cw" ${st.keypass_set ? '' : 'disabled'}>Create wallet</button></div>`;
    $('#cw', main()).onclick = () => openModal({ title: 'Create wallet', submit: 'Create',
      body: `<p class="muted tiny">Type the password you saved in <span class="mono">XMR_KEYPASS</span>. It encrypts the seed and is asked again for every transfer and seed download. If you lose it, nobody can recover the seed.</p>${fld('Password', '<input name="password" type="password" autocomplete="off" required>')}`,
      onSubmit: async (d) => {
        const r = await api('/crypto/wallet', { method: 'POST', body: { password: d.password } });
        toast('Wallet created ✨');
        askSeed(r.wallet && r.wallet.address);
        draw();
      } });
  }
  function askSeed() {
    openModal({ title: 'Back up your seed now', submit: 'Download seed', cancel: 'Later',
      body: '<p class="muted">Download the 25-word seed and keep it offline. Anyone with it can spend the wallet.</p>' + fld('Password', '<input name="password" type="password" autocomplete="off" required>'),
      onSubmit: async (d) => { await downloadSeed(d.password); } });
  }
  async function downloadSeed(password) {
    const r = await api('/crypto/seed', { method: 'POST', body: { password } });
    const text = `Monero wallet seed (25 words) — KEEP SECRET\n\n${r.seed}\n\nMain address: ${r.address}\nRestore height: ${r.restore_height}\n`;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    a.download = 'monero-wallet-seed.txt';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast('Seed downloaded — store it safely');
  }

  // ---------------------------------------------------------------- wallet home
  async function home() {
    const w = st.wallet || {};
    main().innerHTML = `<div class="card stack"><div class="row between"><h3>👛 Wallet</h3><span class="badge ok">✅ Wallet configured</span></div>
      <div class="chips"><span class="badge ${st.enabled ? 'ok' : 'warn'}">${st.enabled ? 'Crypto payments ON' : 'Crypto payments OFF'}</span><span class="badge">${fmtNum(st.open_invoices)} open orders</span><span class="badge ${st.running ? 'ok' : ''}">${st.running ? 'engine running' : 'engine idle'}</span></div>
      <div class="tiny muted">Main address</div><div class="mono wrap" data-copy-text="${esc(w.address || '')}" style="word-break:break-all;cursor:pointer">${esc(w.address || '—')} 📋</div>
      <div class="tiny muted">Created ${fmtDate(w.created_at)} · scanning from block ${fmtNum(w.restore_height)} · node ${esc(st.node)}</div>
      ${st.poll_error ? `<p class="tiny" style="color:var(--danger,#e5484d)">⚠ ${esc(st.poll_error)}</p>` : ''}</div>
      <div class="grid cols-3 gap-top" id="bal">${skeleton(1)}</div>
      <div class="row gap-top"><button class="btn primary" id="sw">📤 Transfer to main account</button><button class="btn" id="sd">🔐 Download seed phrase</button><button class="btn" id="rf">↻ Refresh balance</button></div>
      <div class="section-title"><h2>💰 Money received</h2><div id="rb"></div></div>
      <div class="grid cols-3" id="stats">${skeleton(1)}</div><div id="list" class="list gap-top"></div><div id="pg"></div>`;
    balance();
    $('#rf', main()).onclick = balance;
    $('#sd', main()).onclick = () => openModal({ title: 'Download seed phrase', submit: 'Download',
      body: '<p class="muted tiny">Needs your password. The download is logged.</p>' + fld('Password', '<input name="password" type="password" autocomplete="off" required>'),
      onSubmit: async (d) => { await downloadSeed(d.password); } });
    $('#sw', main()).onclick = () => openModal({ title: 'Transfer to main account', submit: 'Transfer', danger: true,
      body: `<p class="muted tiny">Sends the whole <b>unlocked</b> balance to the main Monero address saved in the Settings tab${st.main_address ? ` (<span class="mono">${esc(st.main_address.slice(0, 10))}…${esc(st.main_address.slice(-6))}</span>)` : ' — <b>none saved yet</b>'}. Network fee is taken from the amount.</p>
        ${fld('Override address (optional)', '<input name="address" autocomplete="off" placeholder="leave empty to use the saved main address">')}${fld('Password', '<input name="password" type="password" autocomplete="off" required>')}`,
      onSubmit: async (d) => {
        const r = await api('/crypto/sweep', { method: 'POST', body: { password: d.password, address: d.address.trim() || undefined } });
        toast(`Sent ${xmrFmt(r.xmr)} XMR ✨`);
        balance();
      } });
    let rng = { range: 'all', start: '', end: '' }, page = 1;
    const bar = rangeBar((r) => { rng = r; page = 1; stats(); }, { initial: 'all' });
    $('#rb', main()).appendChild(bar.el);
    async function stats() {
      try {
        const q = rng.range === 'custom' ? { start: rng.start, end: rng.end } : {};
        const [s, l] = await Promise.all([api('/crypto/stats', { params: q }), api('/crypto/received', { params: { ...rng, page } })]);
        const tile = (lbl, v, hot) => tileHtml(lbl, `${xmrFmt(v.xmr)} XMR`, `${usd(v.usd)} · ${fmtMoney(v.inr)} · ${fmtNum(v.count)} payments`, { hot });
        $('#stats', main()).innerHTML = tile('All time', s.all, true) + tile('Today', s.today) + tile('Yesterday', s.yesterday) + (s.custom ? tile(`${esc(rng.start)} → ${esc(rng.end)}`, s.custom, true) : '');
        $('#list', main()).innerHTML = l.items.length ? l.items.map((r) => `<div class="li wide">${avatar('', (r.user || {}).full_name, 'lg')}<div class="meta"><b>${esc((r.user || {}).full_name || '—')}${(r.user || {}).username ? ' · @' + esc(r.user.username) : ''}</b>
          <small>${r.kind === 'deposit' ? '👛 Wallet deposit' : '📱 ' + esc(r.app_name || 'Order')} · ${fmtDate(r.ts)}</small><small class="mono">${esc(String(r.txn_id || ''))}</small></div><div class="acts"><b>${xmrFmt(r.xmr)} XMR</b><span class="badge">${usd(r.usd)}</span></div></div>`).join('')
          : empty('🪙', 'No crypto payments in this period.');
        pager($('#pg', main()), l, (p) => { page = p; stats(); });
      } catch (e) { toast(e.message, 'err'); }
    }
    stats();
  }
  async function balance() {
    const box = $('#bal', main());
    if (!box) return;
    box.innerHTML = skeleton(1);
    try {
      const b = await api('/crypto/balance');
      box.innerHTML = tileHtml('Balance', `${xmrFmt(b.total_xmr)} XMR`, b.total_usd != null ? usd(b.total_usd) : 'USD rate unavailable', { big: true }) +
        tileHtml('Ready to transfer', `${xmrFmt(b.unlocked_xmr)} XMR`, b.unlocked_usd != null ? usd(b.unlocked_usd) : '', { hot: true }) +
        (b.fixed_inr_rate ? tileHtml('Value at fixed deposit rate', fmtMoney(b.total_inr_fixed), `1 XMR = ${fmtMoney(b.fixed_inr_rate)} (set in Settings)`) : '') +
        tileHtml('Still confirming', `${xmrFmt(b.locked_xmr)} XMR`, 'unlocks after ~10 blocks (≈20 min)');
    } catch (e) { box.innerHTML = `<div class="card">${empty('⏳', e.message)}</div>`; }
  }

  // ---------------------------------------------------------------- orders
  async function orders() {
    let status = '', page = 1;
    main().innerHTML = `<div class="row"><select id="fs"><option value="">All orders</option><option value="pending">Waiting for payment</option><option value="verification">Confirming</option><option value="success">Paid</option><option value="failed">Expired / cancelled</option></select></div><div id="list" class="list gap-top"></div><div id="pg"></div>`;
    $('#fs', main()).onchange = (e) => { status = e.target.value; page = 1; load(); };
    async function load() {
      try {
        const d = await api('/crypto/invoices', { params: { status, page } });
        $('#list', main()).innerHTML = d.items.length ? d.items.map((t) => { const s = STATUS[t.status] || [t.status, '']; return `<div class="li wide"><div class="avatar">₿</div><div class="meta">
          <b>${esc(t.user.full_name || '—')}${t.user.username ? ' · @' + esc(t.user.username) : ''}</b><small>📱 ${esc(t.app_name)} · ${esc(t.plan_label)} · ${fmtDate(t.created_at)}</small>
          <small class="mono" style="word-break:break-all">${esc(t.address)}</small></div><div class="acts"><b>${xmrFmt(t.xmr)} XMR</b><span class="badge">${usd(t.usd)}</span><span class="badge ${s[1]}">${s[0]}${t.fail_type ? ' · ' + esc(t.fail_type) : ''}</span></div></div>`; }).join('')
          : empty('🧾', 'No crypto orders yet.');
        pager($('#pg', main()), d, (p) => { page = p; load(); });
      } catch (e) { toast(e.message, 'err'); }
    }
    load();
  }

  // ---------------------------------------------------------------- transfers history
  async function moves() {
    const { items } = await api('/crypto/sweeps');
    main().innerHTML = items.length ? `<div class="list">${items.map((r) => `<div class="li wide"><div class="avatar">📤</div><div class="meta"><b>${xmrFmt(r.xmr)} XMR</b>
      <small>${fmtDate(r.ts)} · by ${esc(r.by || '—')} · fee ${xmrFmt(r.fee_xmr)} XMR</small><small class="mono" style="word-break:break-all">${esc(r.to || '')}</small>
      ${(r.txids || []).map((x) => `<small class="mono" data-copy-text="${esc(x)}" style="word-break:break-all">tx ${esc(x)}</small>`).join('')}</div></div>`).join('')}</div>`
      : empty('📤', 'No transfers yet.');
  }
  draw();
}

// -------------------------------------------------------------------- gateway settings (used by the Crypto page and Settings → Monero)
export async function cryptoSettings(box, st, after) {
  if (!isSuper()) { box.innerHTML = empty('🔒', 'Only the Super Admin can change crypto settings.'); return; }
  st = st || await api('/crypto/status');
  box.innerHTML = `<div class="card stack"><h3>₿ Monero payment gateway</h3>
    <p class="muted tiny">When on, plans that allow crypto show a <b>🪙 Crypto</b> button in the bot. Orders are checked automatically (1 confirmation) and keys are delivered with no approval. Bonus (check-in, spin) and referral wallet amounts can’t be used for crypto orders.</p>
    <label class="row"><span class="switch"><input type="checkbox" id="en" ${st.enabled ? 'checked' : ''}><i></i></span> Accept crypto payments</label>
    <div class="two">${fld('₹ per 1 USD', `<input id="rate" type="number" step="0.01" min="1" value="${esc(st.inr_per_usd)}">`, st.rate_set ? 'Plans priced in ₹ are divided by this for crypto.' : '⚠ Not set yet — a default is used. Set your own.')}
    ${fld('Minimum order (USD)', `<input id="mn" type="number" step="0.01" min="0.1" value="${esc(st.min_usd)}">`, 'Smaller orders are refused (minimum $0.10).')}</div>
    <div class="card stack gap-top"><h3>👛 Wallet deposits in crypto</h3>
      <p class="muted tiny">Users can top up their bot wallet with Monero. Their wallet is credited at <b>this fixed rate</b> — <u>not</u> the live market price — so what they see in the bot is exactly what they get. Change it whenever you want; open orders keep the rate they were created with. Leave empty / 0 to switch crypto deposits off.</p>
      <label class="row"><span class="switch"><input type="checkbox" id="den" ${st.deposit_enabled ? 'checked' : ''}><i></i></span> Allow crypto wallet deposits</label>
      ${fld('Fixed rate: ₹ per 1 XMR', `<input id="xi" type="number" step="0.01" min="0" value="${esc(st.xmr_inr || '')}" placeholder="e.g. 15000">`, st.xmr_inr ? `1 XMR = ₹${Number(st.xmr_inr).toLocaleString('en-IN')} → ₹100 deposit = ${(100 / st.xmr_inr).toFixed(8)} XMR` : '⚠ Not set — crypto deposits are hidden in the bot until you set it.')}</div>
    ${fld('Main Monero address (for “Transfer to main account”)', `<input id="ma" autocomplete="off" value="${esc(st.main_address)}" placeholder="4… or 8…">`)}
    <div class="tiny muted">Payment window ${esc(st.window_minutes)} min · confirmations needed ${esc(st.confirmations)} · node ${esc(st.node)}</div>
    <div class="row"><button class="btn primary" id="sv">Save</button></div></div>`;
  $('#sv', box).onclick = async () => {
    try {
      await api('/crypto/settings', { method: 'PUT', body: { enabled: $('#en', box).checked, inr_per_usd: $('#rate', box).value, min_usd: $('#mn', box).value, xmr_inr: $('#xi', box).value, deposit_enabled: $('#den', box).checked, main_address: $('#ma', box).value.trim() } });
      toast('Saved ✨');
      if (after) after();
    } catch (e) { toast(e.message, 'err'); }
  };
}
