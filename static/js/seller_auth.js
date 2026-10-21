// Seller sign-in / sign-up (JSON calls; CSRF token comes from the page's meta tag).
const csrf = document.querySelector('meta[name=csrf-token]').content;
const $ = (s) => document.querySelector(s);
let mode = 'login';
const err = (m) => { const e = $('#err'); e.textContent = m || ''; e.hidden = !m; };

function setMode(m) {
  mode = m;
  document.querySelectorAll('#mode button').forEach((b) => b.classList.toggle('on', b.dataset.m === m));
  $('#nameRow').hidden = $('#confirmRow').hidden = m !== 'signup';
  $('#go').textContent = m === 'signup' ? 'Create my account 🚀' : 'Sign in →';
  $('#pw').autocomplete = m === 'signup' ? 'new-password' : 'current-password';
  err('');
}
$('#mode').addEventListener('click', (e) => { const b = e.target.closest('[data-m]'); if (b) setMode(b.dataset.m); });
$('#pwToggle').addEventListener('click', () => { const p = $('#pw'); p.type = p.type === 'password' ? 'text' : 'password'; });

async function call(path, body) {
  const res = await fetch('/seller/api' + path, { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
  let d = {}; try { d = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error(d.error || d.message || 'Something went wrong.');
  return d;
}

$('#form').addEventListener('submit', async (e) => {
  e.preventDefault(); err('');
  const f = Object.fromEntries(new FormData(e.target));
  const btn = $('#go'); btn.disabled = true;
  try {
    if (mode === 'signup') {
      if (f.password !== f.confirm) throw new Error('Passwords do not match.');
      await call('/signup', f);
    }
    await call('/login', { username: f.username, password: f.password });
    location.href = '/seller';
  } catch (ex) { err(ex.message); } finally { btn.disabled = false; }
});
