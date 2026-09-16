/* Mahsulot sahifalari: ko'rinish almashtirish va dinamik rang qatorlari. */
(function () {
  "use strict";

  /* --- Jadval / kartochka ko'rinishi ---------------------------------- */

  var VIEW_KEY = "pos-product-view";
  var viewSwitch = document.getElementById("viewSwitch");

  function applyView(view) {
    document.querySelectorAll("[data-view-body]").forEach(function (el) {
      el.hidden = el.dataset.viewBody !== view;
    });
    if (!viewSwitch) return;
    viewSwitch.querySelectorAll("[data-view]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.view === view));
    });
  }

  if (viewSwitch) {
    var saved = "table";
    try { saved = localStorage.getItem(VIEW_KEY) || "table"; } catch (e) {}
    applyView(saved);

    viewSwitch.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-view]");
      if (!btn) return;
      applyView(btn.dataset.view);
      try { localStorage.setItem(VIEW_KEY, btn.dataset.view); } catch (err) {}
    });
  }

  /* --- Savdo turiga qarab maydonlarni ko'rsatish ---------------------- */

  var branchSelect = document.getElementById("id_branch");
  var tradeNode = document.getElementById("branch-trades");
  var fieldsNode = document.getElementById("trade-fields");

  if (branchSelect && tradeNode && fieldsNode) {
    var BRANCH_TRADES = JSON.parse(tradeNode.textContent);
    var TRADE_FIELDS = JSON.parse(fieldsNode.textContent);
    var hint = document.getElementById("tradeHint");
    var empty = document.getElementById("tradeEmpty");
    var tradeNames = {
      "1": "Universal", "2": "Oziq-ovqat", "3": "Qurilish mollari",
      "4": "Kiyim-kechak va poyabzal", "5": "Farmasevtika", "6": "Texnika"
    };

    function applyTrade() {
      var trade = BRANCH_TRADES[branchSelect.value];
      var allowed = trade ? (TRADE_FIELDS[String(trade)] || []) : [];

      document.querySelectorAll(".trade-field").forEach(function (field) {
        field.hidden = allowed.indexOf(field.dataset.field) === -1;
      });

      var hasFields = allowed.length > 0;
      if (empty) empty.hidden = hasFields;
      if (hint) hint.textContent = trade ? tradeNames[String(trade)] : "filialni tanlang";
    }

    branchSelect.addEventListener("change", applyTrade);
    applyTrade();
  }

  /* --- Rang: mavjud rang tanlansa kodi ham to'g'rilansin -------------- */

  var colorOptions = {};
  document.querySelectorAll("#color-options option").forEach(function (opt) {
    colorOptions[opt.value.toLowerCase()] = opt.dataset.hex || "";
  });

  function bindColorSync(scope) {
    scope.querySelectorAll('input[name$="-color"]').forEach(function (input) {
      if (input.dataset.colorBound) return;
      input.dataset.colorBound = "1";
      input.addEventListener("input", function () {
        var hex = colorOptions[input.value.trim().toLowerCase()];
        if (!hex) return;
        var row = input.closest(".variant-row");
        var picker = row && row.querySelector('input[type="color"]');
        if (picker) picker.value = hex;
      });
    });
  }

  bindColorSync(document);

  /* --- Rang qatorlari (Django formset) -------------------------------- */

  var rows = document.getElementById("variantRows");
  var addBtn = document.getElementById("addVariant");
  var tpl = document.getElementById("variantTemplate");
  var totalInput = document.getElementById("id_variant-TOTAL_FORMS");

  function refreshRemoveButtons() {
    if (!rows) return;
    var all = rows.querySelectorAll(".variant-row");
    all.forEach(function (row) {
      row.querySelector(".row-remove").disabled = all.length === 1;  // oxirgi qator o'chmasin
    });
  }

  // Tanlangan rasmni qator ichida ko'rsatish.
  function bindPreview(scope) {
    scope.querySelectorAll('input[type="file"]').forEach(function (input) {
      if (input.dataset.bound) return;
      input.dataset.bound = "1";
      input.addEventListener("change", function () {
        var box = input.closest(".image-cell").querySelector(".image-preview");
        var file = input.files && input.files[0];
        if (!file) { box.style.backgroundImage = ""; box.classList.remove("has-image"); return; }
        box.style.backgroundImage = 'url("' + URL.createObjectURL(file) + '")';
        box.classList.add("has-image");
      });
    });
  }

  if (rows && addBtn && tpl && totalInput) {
    bindPreview(rows);
    refreshRemoveButtons();

    addBtn.addEventListener("click", function () {
      var index = parseInt(totalInput.value, 10);
      // Django empty_form'dagi __prefix__ o'rniga yangi qator raqami qo'yiladi.
      var html = tpl.innerHTML.replace(/__prefix__/g, String(index));
      var holder = document.createElement("div");
      holder.innerHTML = html.trim();
      var row = holder.firstElementChild;

      rows.appendChild(row);
      totalInput.value = index + 1;
      bindPreview(row);
      bindColorSync(row);
      refreshRemoveButtons();

      var colorInput = row.querySelector('input[name$="-color"]');
      if (colorInput) colorInput.focus();
    });

    rows.addEventListener("click", function (e) {
      var btn = e.target.closest(".row-remove");
      if (!btn || btn.disabled) return;

      btn.closest(".variant-row").remove();
      // Formset indekslari uzluksiz bo'lishi kerak — qolgan qatorlarni qayta raqamlaymiz.
      rows.querySelectorAll(".variant-row").forEach(function (row, i) {
        row.querySelectorAll("input, select").forEach(function (field) {
          if (field.name) field.name = field.name.replace(/variant-\d+-/, "variant-" + i + "-");
          if (field.id) field.id = field.id.replace(/variant-\d+-/, "variant-" + i + "-");
        });
      });
      totalInput.value = rows.querySelectorAll(".variant-row").length;
      refreshRemoveButtons();
    });
  }
})();
