document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('filterForm');
  if (!form) { return; }

  var range = document.getElementById('maxPrice');
  var label = document.getElementById('maxPriceLabel');
  var sort = document.getElementById('sortBy');

  if (range && label) {
    range.addEventListener('input', function () { label.textContent = range.value; });
  }
  form.addEventListener('change', function () { form.submit(); });
  if (sort) {
    sort.addEventListener('change', function () { form.submit(); });
  }
});