document.addEventListener('DOMContentLoaded', function () {
  var dateInput = document.getElementById('date');
  var source = document.getElementById('source');
  var destination = document.getElementById('destination');
  var swapBtn = document.getElementById('swapBtn');
  var form = document.getElementById('searchForm');

  if (dateInput) {
    var today = new Date();
    var iso = today.getFullYear() + '-' +
      String(today.getMonth() + 1).padStart(2, '0') + '-' +
      String(today.getDate()).padStart(2, '0');
    dateInput.min = iso;
    if (!dateInput.value) { dateInput.value = iso; }
  }

  if (swapBtn && source && destination) {
    swapBtn.addEventListener('click', function () {
      var temp = source.value;
      source.value = destination.value;
      destination.value = temp;
    });
  }

  if (form && source && destination) {
    form.addEventListener('submit', function (e) {
      if (source.value.trim().toLowerCase() === destination.value.trim().toLowerCase()) {
        e.preventDefault();
        alert('Source and destination cannot be the same.');
      }
    });
  }
});

// Prevent double submit on POST forms
document.addEventListener('submit', function (e) {
  var form = e.target;
  if (!form.method || form.method.toLowerCase() !== 'post') { return; }
  setTimeout(function () {
    if (e.defaultPrevented) { return; }
    form.querySelectorAll('button[type="submit"]').forEach(function (b) { b.disabled = true; });
  }, 0);
});
window.addEventListener('pageshow', function (e) {
  if (e.persisted) {
    document.querySelectorAll('button[type="submit"]').forEach(function (b) { b.disabled = false; });
  }
});