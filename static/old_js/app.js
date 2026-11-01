import { $, $$, api, clearTimers, copyText, debounce, esc, openModal, skeleton, state, toast, empty } from './core.js';
import { bindUserList, userItem } from './licenseui.js';

const routes = [
  [/^(home)?$/, 'home', () => import('./dashboard.js')],
  [/^apps\/([\w-]+)$/, 'home', () => import('./appview.js')],
  [/^users$/, 'users', () => import('./users.js')],
  [/^earning$/, 'earning', () => import('./earning.js')],
  [/^upi$/, 'upi', () => import('./upi.js')],
  [/^plans$/, 'plans', () => import('./plans.js')],
  [/^logs$/, 'logs', () => import('./logs.js')],
  [/^settings$/, 'settings', () => import('./settings.js')],
];

const view = $('#view');
let navToken = 0;

async function route() {
  const token = ++navToken;
  clearTimers();
  closeDrawer();
  const hash = location.hash.replace(/^#\/?/, '');
  const hit = routes.map(([re, nav, load]) => [re.exec(hash), nav, load]).find(([m]) => m);
  $$('#nav a').forEach((a) => a.classList.toggle('active', hit && a.dataset.route === hit[1]));
  if (!hit) { view.innerHTML = empty('🛸', 'Page not found'); return; }
  view.innerHTML = skeleton(3);
  window.scrollTo({ top: 0 });
  try {
    const mod = await hit[2]();
    if (token !== navToken) return;
    view.innerHTML = '';
    view.style.animation = 'none'; void view.offsetWidth; view.style.animation = '';
    await mod.default(view, ...hit[0].slice(1));
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
  try { await api('/logout', { method: 'POST' }); } finally { location.href = '/login'; }
});

// ---- global search (name / @username / chat id / license key)
const search = $('#globalSearch');
const runSearch = debounce(async () => {
  const q = search.value.trim();
  if (q.length < 2) return;
  const m = openModal({ title: `Results for “${q}”`, wide: true, noFoot: true, body: skeleton(3) });
  try {
    const { items } = await api('/search', { params: { q } });
    const box = $('form', m.el);
    box.innerHTML = items.length ? `<div class="list" id="gsList">${items.map(userItem).join('')}</div><p class="tiny muted">Tap a license to renew, revoke or reset its device.</p>`
      : empty('🕵️', 'No users or license keys match that.');
    bindUserList(box, items, () => { m.close(); runSearch(); });
  } catch (e) { toast(e.message, 'err'); m.close(); }
}, 500);
search.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } });

// ---- delegated copy buttons anywhere: <button data-copy-text="...">
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-copy-text]');
  if (b) copyText(b.dataset.copyText);
});

(async function boot() {
  try {
    const me = await api('/me');
    state.me = me; state.csrf = me.csrf;
    $('#meName').textContent = me.username;
    $('#meRole').textContent = me.role === 'admin' ? 'Admin' : 'View only';
    $('#meAvatar').textContent = me.username.charAt(0).toUpperCase();
    if (me.role !== 'admin') document.body.classList.add('viewer');
    window.addEventListener('hashchange', route);
    route();
  } catch (e) { view.innerHTML = empty('🔒', e.message); }
})();
