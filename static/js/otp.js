document.addEventListener('DOMContentLoaded', function () {
  var btn = document.getElementById('resendBtn');
  var note = document.getElementById('resendNote');
  var code = document.getElementById('id_code');
  if (code) { code.focus(); }
  if (!btn) { return; }

  var left = parseInt(btn.dataset.cooldown, 10) || 0;
  function tick() {
    if (left <= 0) {
      btn.disabled = false;
      note.textContent = '';
      return;
    }
    btn.disabled = true;
    note.textContent = ' available in ' + left + 's';
    left -= 1;
    setTimeout(tick, 1000);
  }
  tick();
});