/* Nakladnoyni ko'rib chiqish: qator qo'shish, o'chirish belgisi va jami summa.
   Summalar faqat ko'rsatish uchun — haqiqiy hisob tasdiqlashda serverda. */
(function () {
  "use strict";

  var form = document.getElementById("wbForm");
  if (!form) return;

  var rows = document.getElementById("wbRows");
  var template = document.getElementById("wbEmptyRow");
  var totalInput = form.querySelector('input[name="items-TOTAL_FORMS"]');
  var totalLabel = document.getElementById("wbTotal");
  var currency = (totalLabel.textContent.match(/[^\d\s,.]+.*$/) || [""])[0].trim();

  function number(value) {
    var parsed = parseFloat(String(value || "").replace(",", "."));
    return isNaN(parsed) ? 0 : parsed;
  }

  function spaced(value) {
    return Math.round(value).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function field(row, suffix) {
    return row.querySelector('[name$="-' + suffix + '"]');
  }

  function recalc() {
    var total = 0;
    Array.prototype.forEach.call(rows.querySelectorAll(".wb-row"), function (row) {
      var removed = field(row, "DELETE");
      var line = number(field(row, "quantity").value) * number(field(row, "price").value);
      row.classList.toggle("removed", !!(removed && removed.checked));
      row.querySelector(".wb-line").textContent = line ? spaced(line) : "—";
      if (!(removed && removed.checked)) total += line;
    });
    totalLabel.textContent = spaced(total) + (currency ? " " + currency : "");
  }

  function prepare(row) {
    var barcode = field(row, "barcode");
    if (barcode) barcode.placeholder = "shtrix-kod";
  }

  document.getElementById("wbAddRow").addEventListener("click", function () {
    var index = parseInt(totalInput.value, 10) || 0;
    var html = template.innerHTML.replace(/__prefix__/g, index);
    var holder = document.createElement("tbody");
    holder.innerHTML = html.trim();
    var row = holder.firstElementChild;
    rows.appendChild(row);
    totalInput.value = index + 1;
    prepare(row);
    field(row, "name").focus();
  });

  rows.addEventListener("input", recalc);
  rows.addEventListener("change", recalc);

  // Tasdiqlash va bekor qilish — qaytarib bo'lmaydigan amallar.
  document.getElementById("wbConfirm").addEventListener("click", function (event) {
    if (!window.confirm("Tasdiqlansinmi? Qoldiq va hisob o'zgaradi.")) event.preventDefault();
  });
  document.getElementById("wbCancel").addEventListener("click", function (event) {
    if (!window.confirm("Nakladnoy bekor qilinsinmi?")) event.preventDefault();
  });

  Array.prototype.forEach.call(rows.querySelectorAll(".wb-row"), prepare);
  recalc();
})();
