// Seller sign-in (accounts are created inside the Telegram bot: "Become Seller").
const csrf = document.querySelector('meta[name=csrf-token]').content;
const $ = (s) => document.querySelector(s);
const err = (m) => { const e = $('#err'); e.textContent = m || ''; e.hidden = !m; };
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
  try { await call('/login', { username: f.username, password: f.password }); location.href = document.body.dataset.home || '/seller'; }
  catch (ex) { err(ex.message); } finally { btn.disabled = false; }
});
