document.addEventListener('DOMContentLoaded', function () {
  var radios = document.querySelectorAll('input[name="method"]');
  var panels = document.querySelectorAll('.pay-panel');
  var payBtn = document.getElementById('payBtn');

  function update() {
    var checked = document.querySelector('input[name="method"]:checked');
    var method = checked ? checked.value : 'UPI';
    panels.forEach(function (panel) {
      panel.classList.toggle('d-none', panel.dataset.method !== method);
    });
    if (payBtn) {
      payBtn.textContent = method === 'COUNTER'
        ? 'Confirm Booking (Pay at Counter)'
        : 'Pay \u20B9' + payBtn.dataset.amount;
    }
  }
  radios.forEach(function (r) { r.addEventListener('change', update); });
  update();

  var card = document.getElementById('id_card_number');
  if (card) {
    card.addEventListener('input', function () {
      var v = card.value.replace(/\D/g, '').slice(0, 16);
      card.value = v.replace(/(.{4})/g, '$1 ').trim();
    });
  }
  var expiry = document.getElementById('id_expiry');
  if (expiry) {
    expiry.addEventListener('input', function () {
      var v = expiry.value.replace(/\D/g, '').slice(0, 4);
      if (v.length > 2) { v = v.slice(0, 2) + '/' + v.slice(2); }
      expiry.value = v;
    });
  }

  var countdown = document.getElementById('countdown');
  if (countdown) {
    var left = parseInt(countdown.dataset.seconds, 10);
    var tick = function () {
      if (left <= 0) { window.location.reload(); return; }
      var m = Math.floor(left / 60);
      var s = left % 60;
      countdown.textContent = m + ':' + String(s).padStart(2, '0');
      left -= 1;
    };
    tick();
    setInterval(tick, 1000);
  }
});