// App shell: navigation + router for the admin, reseller and seller panels (same code, different tables).
import { $, $$, api, clearTimers, copyText, debounce, dueDialog, empty, esc, openModal, skeleton, state, toast, fmtDate } from './core.js';

const P = state.panel;
const page = (name) => () => import(`./pages/${name}.js`);

const PANELS = {
  admin: {
    nav: [['home', '🏠', 'Home', '#/'], ['users', '👥', 'Users', '#/users'], ['earning', '💸', 'Earning', '#/earning'],
      ['upi', '🏦', 'UPI ID', '#/upi'], ['plans', '🏷️', 'Plans', '#/plans'], ['purchase', '🛒', 'Purchase', '#/purchase'],
      ['balance', '👛', 'Balance', '#/balance'], ['checkin', '📅', 'Daily Check-in', '#/checkin'], ['spin', '🎡', 'Spin Wheel', '#/spin'],
      ['referral', '🎁', 'Referral', '#/referral'], ['leaderboard', '🏆', 'Leaderboard', '#/leaderboard'],
      ['broadcast', '📣', 'Broadcast', '#/broadcast'], ['promotion', '🔥', 'Promotion', '#/promotion'], ['shortener', '🔗', 'Shortener', '#/shortener'], ['reseller', '🏪', 'Reseller', '#/reseller'], ['seller', '🚀', 'Seller', '#/seller'],
      ['logs', '📜', 'Logs', '#/logs'], ['settings', '⚙️', 'Settings', '#/settings']],
    routes: [[/^(home)?$/, 'home', 'a_home'], [/^apps\/([\w-]+)$/, 'purchase', 'a_app'], [/^users$/, 'users', 'users'],
      [/^earning$/, 'earning', 'earning'], [/^upi$/, 'upi', 'upi'], [/^plans$/, 'plans', 'a_plans'],
      [/^purchase$/, 'purchase', 'a_purchase'], [/^balance$/, 'balance', 'balance'], [/^(checkin|spin)$/, null, 'a_rewards'],
      [/^referral$/, 'referral', 'referral'], [/^leaderboard$/, 'leaderboard', 'a_leaderboard'],
      [/^broadcast$/, 'broadcast', 'a_broadcast'], [/^promotion$/, 'promotion', 'a_promotion'], [/^shortener$/, 'shortener', 'shortener'], [/^reseller$/, 'reseller', 'a_resellers'],
      [/^reseller\/([\w-]+)$/, 'reseller', 'a_reseller'], [/^seller$/, 'seller', 'a_sellers'],
      [/^seller\/review\/([\w-]+)$/, 'seller', 'a_review'], [/^seller\/([\w-]+)$/, 'seller', 'a_seller'],
      [/^logs$/, 'logs', 'a_logs'], [/^settings$/, 'settings', 'a_settings']],
  },
  reseller: {
    nav: [['home', '🏠', 'Home', '#/'], ['users', '👥', 'Users', '#/users'], ['earning', '💸', 'Earning', '#/earning'],
      ['upi', '🏦', 'UPI ID', '#/upi'], ['prices', '🏷️', 'Prices', '#/prices'], ['referral', '🎁', 'Referral', '#/referral'],
      ['balance', '👛', 'Balance', '#/balance'], ['due', '🧾', 'Due', '#/due'], ['support', '🛟', 'Support', '#/support'],
      ['settings', '⚙️', 'Settings', '#/settings']],
    routes: [[/^(home)?$/, 'home', 'r_home'], [/^users$/, 'users', 'users'], [/^earning$/, 'earning', 'earning'],
      [/^upi$/, 'upi', 'upi'], [/^prices$/, 'prices', 'r_prices'], [/^referral$/, 'referral', 'referral'],
      [/^balance$/, 'balance', 'balance'], [/^due$/, 'due', 'r_due'], [/^support$/, 'support', 'r_support'],
      [/^settings$/, 'settings', 'r_settings']],
  },
  seller: {
    nav: [['home', '📊', 'Dashboard', '#/'], ['create', '🧩', 'Create API', '#/create'], ['publish', '📤', 'Publish App', '#/publish'],
      ['docs', '📚', 'Documentation', '#/docs'], ['apps', '📱', 'My Apps', '#/apps'], ['wallet', '👛', 'Wallet', '#/wallet'],
      ['broadcast', '📣', 'Broadcast', '#/broadcast'], ['settings', '⚙️', 'Settings', '#/settings']],
    routes: [[/^(home)?$/, 'home', 's_dash'], [/^create$/, 'create', 's_create'], [/^publish(?:\/([\w-]+))?$/, 'publish', 's_publish'],
      [/^docs$/, 'docs', 's_docs'], [/^apps$/, 'apps', 's_apps'], [/^apps\/([\w-]+)$/, 'apps', 's_app'],
      [/^wallet$/, 'wallet', 's_wallet'], [/^broadcast$/, 'broadcast', 's_broadcast'], [/^settings$/, 'settings', 's_settings']],
  },
};
const cfg = PANELS[P];
const view = $('#view');
let navToken = 0;

$('#nav').innerHTML = cfg.nav.map(([k, i, l, h]) => `<a href="${h}" data-route="${k}"><i>${i}</i>${l}<span class="nav-badge" data-badge="${k}" hidden></span></a>`).join('');

async function route() {
  const token = ++navToken;
  clearTimers();
  closeDrawer();
  const hash = location.hash.replace(/^#\/?/, '');
  const hit = cfg.routes.map(([re, nav, mod]) => [re.exec(hash), nav, mod]).find(([m]) => m);
  $$('#nav a').forEach((a) => a.classList.toggle('active', !!hit && (hit[1] ? a.dataset.route === hit[1] : a.dataset.route === hash)));
  if (!hit) { view.innerHTML = empty('🛸', 'Page not found'); return; }
  view.innerHTML = skeleton(3);
  window.scrollTo({ top: 0 });
  try {
    const mod = await page(hit[2])();
    if (token !== navToken) return;
    // every page renders into its own holder: a slow request of the page we just left can never touch the new page
    const holder = document.createElement('div');
    view.replaceChildren(holder);
    view.style.animation = 'none'; void view.offsetWidth; view.style.animation = '';
    await mod.default(holder, ...hit[0].slice(1));
  } catch (e) {
    if (token === navToken) view.innerHTML = empty('💥', e.message || 'Something went wrong');
  }
}

// ---- drawer
const drawer = $('#drawer'), scrim = $('#scrim');
function openDrawer() { drawer.classList.add('open'); scrim.classList.add('show'); }
function closeDrawer() { drawer.classList.remove('open'); scrim.classList.remove('show'); }
$('#menuBtn').addEventListener('click', openDrawer);
scrim.addEventListener('click', closeDrawer);

// ---- theme
const applyTheme = (t) => { document.documentElement.dataset.theme = t; try { localStorage.setItem('gz-theme', t); } catch { /* private mode */ } };
try { applyTheme(localStorage.getItem('gz-theme') || 'dark'); } catch { /* ignore */ }
$('#themeBtn').addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

// ---- logout
$('#logoutBtn').addEventListener('click', async () => {
  try { await api('/logout', { method: 'POST' }); } finally { location.href = document.body.dataset.login || (P === 'admin' ? '/login' : '/' + P); }
});

// ---- global search (admin + reseller): name / @username / chat id / license key -> full user details
async function setupSearch() {
  const search = $('#globalSearch');
  const { userRow, bindUserRows } = await import('./pages/userui.js');
  const run = debounce(async () => {
    const q = search.value.trim();
    if (q.length < 2) return;
    const m = openModal({ title: `Results for “${q}”`, wide: true, noFoot: true, body: skeleton(3) });
    try {
      const { items } = await api('/search', { params: { q } });
      const box = $('form', m.el);
      box.innerHTML = items.length ? `<div class="list" id="gsList">${items.map(userRow).join('')}</div><p class="tiny muted">Tap a user for full details, edit and purchase history.</p>`
        : empty('🕵️', 'No users or license keys match that.');
      bindUserRows(box, items, () => { m.close(); run(); });
    } catch (e) { toast(e.message, 'err'); m.close(); }
  }, 500);
  search.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); run(); } });
}

// ---- seller notifications
async function setupBell() {
  const bell = $('#bellBtn'), dot = $('#bellDot');
  bell.hidden = false;
  const refresh = async () => { try { const d = await api('/notifications'); dot.hidden = !d.unread; return d; } catch { return null; } };
  bell.addEventListener('click', async () => {
    const d = await refresh();
    if (!d) return;
    openModal({ title: 'Notifications', noFoot: true, wide: false, body: d.items.length
      ? `<div class="list">${d.items.map((n) => `<div class="li wide"><div class="avatar">${n.read ? '🔔' : '🆕'}</div><div class="meta"><b>${esc(n.title)}</b><small>${esc(n.text)}</small><small>${fmtDate(n.ts)}</small></div></div>`).join('')}</div>`
      : empty('🔕', 'Nothing yet.') });
    if (d.unread) { await api('/notifications/read', { method: 'POST' }); dot.hidden = true; }
  });
  refresh();
  setInterval(refresh, 60000);
}

// ---- admin nav badges
async function badges() {
  const set = (k, n) => { const el = $(`[data-badge="${k}"]`); if (el) { el.textContent = n > 99 ? '99+' : n; el.hidden = !n; } };
  try {
    const [s, r, t] = await Promise.all([api('/sellers/overview'), api('/referral'), api('/txns', { params: { status: 'verification' } })]);
    set('seller', s.pending_submissions + s.pending_withdrawals);
    set('referral', r.overview.pending_withdrawals);
    set('upi', t.counts.verification);
  } catch { /* badges are optional */ }
}

// ---- reseller: the moment the due limit is reached, show the "contact support" dialog (once per pause)
let dueShown = false;
function watchDue(me) {
  const check = (m) => {
    if (m.forced && !dueShown) { dueShown = true; dueDialog(m.admin_support); }
    if (!m.forced) dueShown = false;
  };
  check(me);
  setInterval(async () => { try { const m = await api('/me'); state.me = { ...state.me, forced: m.forced, admin_support: m.admin_support }; check(m); } catch { /* ignore */ } }, 45000);
}

document.addEventListener('click', (e) => { const b = e.target.closest('[data-copy-text]'); if (b) copyText(b.dataset.copyText); });

(async function boot() {
  try {
    const me = await api('/me');
    state.me = me; state.csrf = me.csrf;
    const name = me.username || me.name || '';
    $('#meName').textContent = name;
    $('#meRole').textContent = P === 'admin' ? ({ admin: 'Super Admin', admin2: '2nd Admin' }[me.role] || 'View only') : P === 'reseller' ? 'Reseller' : 'Seller';
    $('#meAvatar').textContent = (name || '?').charAt(0).toUpperCase();
    if (P === 'admin' && me.role === 'viewer') document.body.classList.add('viewer');
    if (P === 'admin' && me.role === 'admin2') document.body.classList.add('admin2');
    if (P === 'seller') { $('#searchBox').hidden = true; $('#topSpacer').hidden = false; setupBell(); } else setupSearch();
    if (P === 'admin') { badges(); setInterval(badges, 60000); }
    if (P === 'reseller') watchDue(me);
    window.addEventListener('hashchange', route);
    route();
  } catch (e) { view.innerHTML = empty('🔒', e.message); }
})();
