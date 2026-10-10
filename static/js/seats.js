document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('seatForm');
  if (!form) { return; }

  var max = parseInt(form.dataset.max, 10);
  var price = parseInt(form.dataset.price, 10);
  var statusUrl = form.dataset.statusUrl;
  var seatsInput = document.getElementById('seatsInput');
  var seatList = document.getElementById('seatList');
  var totalEl = document.getElementById('totalAmount');
  var btn = document.getElementById('continueBtn');
  var notice = document.getElementById('seatNotice');
  var selected = {};   // seat id -> seat number
  var noticeTimer = null;

  function showNotice(text) {
    notice.textContent = text;
    notice.classList.remove('d-none');
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(function () { notice.classList.add('d-none'); }, 4000);
  }

  function render() {
    var ids = Object.keys(selected);
    seatsInput.value = ids.join(',');
    seatList.textContent = ids.length ? ids.map(function (id) { return selected[id]; }).join(', ') : 'None';
    totalEl.textContent = ids.length * price;
    btn.disabled = ids.length === 0;
  }

  document.querySelector('.seat-map').addEventListener('click', function (e) {
    var seat = e.target.closest('.seat');
    if (!seat || seat.disabled) { return; }
    var id = seat.dataset.id;
    if (selected[id]) {
      delete selected[id];
      seat.classList.remove('selected');
    } else {
      if (Object.keys(selected).length >= max) {
        showNotice('You can select up to ' + max + ' seats.');
        return;
      }
      selected[id] = seat.dataset.number;
      seat.classList.add('selected');
    }
    render();
  });

  form.addEventListener('submit', function (e) {
    if (!Object.keys(selected).length) {
      e.preventDefault();
      showNotice('Please select at least one seat.');
    }
  });

  // Refresh booked seats every 15 seconds
  function refresh() {
    fetch(statusUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        document.querySelectorAll('.seat').forEach(function (seat) {
          var gender = data.booked[seat.dataset.id];
          if (gender && !seat.classList.contains('booked')) {
            seat.classList.remove('selected');
            seat.classList.add('booked', 'booked-' + gender);
            seat.disabled = true;
            if (selected[seat.dataset.id]) {
              showNotice('Seat ' + selected[seat.dataset.id] + ' was just booked by someone else.');
              delete selected[seat.dataset.id];
            }
          }
        });
        render();
      })
      .catch(function () { /* ignore network errors */ });
  }
  setInterval(refresh, 15000);
});