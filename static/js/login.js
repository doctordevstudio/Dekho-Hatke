(function () {
  var pw = document.getElementById('pw');
  var btn = document.getElementById('pwToggle');
  if (!pw || !btn) return;
  btn.addEventListener('click', function () {
    var show = pw.type === 'password';
    pw.type = show ? 'text' : 'password';
    btn.textContent = show ? '🙈' : '👁';
  });
})();
