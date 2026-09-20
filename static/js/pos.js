/* Kassa: savat brauzerda yig'iladi, yakunlash serverda tekshiriladi.
   Savat sahifa yangilansa ham yo'qolmasligi uchun sessionStorage'da saqlanadi. */
(function () {
  "use strict";

  var config = document.getElementById("pos-config");
  if (!config) return;

  var CURRENCY = config.dataset.currency || "";
  var VAT = parseFloat(config.dataset.vat) || 0;
  var SEARCH_URL = config.dataset.search;
  var CHECKOUT_URL = config.dataset.checkout;
  var CART_KEY = "pos-cart";
  var ALLOW_NEGATIVE = (function () {
    var node = document.getElementById("allow-negative");
    try { return node ? JSON.parse(node.textContent) : false; } catch (e) { return false; }
  })();

  var els = {
    search: document.getElementById("posSearch"),
    results: document.getElementById("posResults"),
    hint: document.getElementById("posHint"),
    rows: document.getElementById("cartRows"),
    empty: document.getElementById("cartEmpty"),
    count: document.getElementById("cartCount"),
    customer: document.getElementById("posCustomer"),
    discount: document.getElementById("posDiscount"),
    autoRow: document.getElementById("autoDiscountRow"),
    autoValue: document.getElementById("sumAutoDiscount"),
    subtotal: document.getElementById("sumSubtotal"),
    vat: document.getElementById("sumVat"),
    total: document.getElementById("sumTotal"),
    payment: document.getElementById("posPayment"),
    cashRow: document.getElementById("cashRow"),
    paid: document.getElementById("posPaid"),
    change: document.getElementById("changeHint"),
    error: document.getElementById("posError"),
    clear: document.getElementById("posClear"),
    checkout: document.getElementById("posCheckout"),
    done: document.getElementById("doneModal"),
    doneMeta: document.getElementById("doneMeta"),
    doneTotal: document.getElementById("doneTotal"),
    doneChangeRow: document.getElementById("doneChangeRow"),
    doneChange: document.getElementById("doneChange"),
    doneDebtRow: document.getElementById("doneDebtRow"),
    doneDebt: document.getElementById("doneDebt"),
    doneReceipt: document.getElementById("doneReceipt"),
    doneNext: document.getElementById("doneNext"),
    doneClose: document.getElementById("doneClose"),
  };

  var PAYMENT = { NAQD: 1, KARTA: 2, OTKAZMA: 3, QARZ: 4 };
  var cart = load();
  var payment = PAYMENT.NAQD;

  /* --- Yordamchilar ---------------------------------------------------- */

  function fmt(value) {
    return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function load() {
    try { return JSON.parse(sessionStorage.getItem(CART_KEY)) || []; } catch (e) { return []; }
  }
  function save() {
    try { sessionStorage.setItem(CART_KEY, JSON.stringify(cart)); } catch (e) {}
  }

  function csrf() {
    var match = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function showError(text) {
    els.error.textContent = text || "";
    els.error.hidden = !text;
  }

  /* --- Savat ----------------------------------------------------------- */

  function add(product, quantity) {
    var step = quantity || 1;
    var row = cart.filter(function (r) { return r.id === product.id; })[0];
    if (row) {
      row.quantity += step;
      row.stock = product.stock;
    } else {
      cart.push({
        id: product.id, name: product.name, price: product.price,
        quantity: step, stock: product.stock, unit: product.unit || "",
      });
    }
    showError("");
    render();
  }

  function setQuantity(id, value) {
    cart = cart.filter(function (row) {
      if (row.id !== id) return true;
      row.quantity = value;
      return value > 0;
    });
    render();
  }

  function remove(id) {
    cart = cart.filter(function (row) { return row.id !== id; });
    render();
  }

  function subtotal() {
    return cart.reduce(function (sum, row) { return sum + row.price * row.quantity; }, 0);
  }

  function autoDiscount() {
    var option = els.customer.options[els.customer.selectedIndex];
    var percent = option ? parseFloat(option.dataset.discount || "0") : 0;
    return percent > 0 ? subtotal() * percent / 100 : 0;
  }

  function manualDiscount() {
    return Math.max(0, parseFloat(els.discount.value) || 0);
  }

  function total() {
    return Math.max(0, subtotal() - manualDiscount() - autoDiscount());
  }

  function rowNode(row) {
    var node = document.createElement("div");
    node.className = "cart-row";
    node.innerHTML =
      '<div class="cart-name">' +
        '<div>' + row.name + '</div>' +
        '<div class="muted">' + fmt(row.price) + " × " + row.quantity +
          (row.unit ? " " + row.unit : "") + '</div>' +
      '</div>' +
      '<div class="qty">' +
        '<button type="button" class="icon-btn" data-step="-1" aria-label="Kamaytirish">−</button>' +
        '<input class="input" type="number" min="0" step="1" value="' + row.quantity + '">' +
        '<button type="button" class="icon-btn" data-step="1" aria-label="Ko\'paytirish">+</button>' +
      '</div>' +
      '<div class="cart-total">' + fmt(row.price * row.quantity) + '</div>' +
      '<button type="button" class="icon-btn row-remove" aria-label="O\'chirish">' +
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>' +
      '</button>';

    node.querySelectorAll("[data-step]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setQuantity(row.id, row.quantity + parseInt(btn.dataset.step, 10));
      });
    });
    node.querySelector("input").addEventListener("change", function (e) {
      setQuantity(row.id, Math.max(0, parseFloat(e.target.value) || 0));
    });
    node.querySelector(".row-remove").addEventListener("click", function () { remove(row.id); });

    if (!ALLOW_NEGATIVE && row.stock != null && row.quantity > row.stock) {
      node.classList.add("over-stock");
      node.title = "Omborda " + fmt(row.stock) + " dona qolgan";
    }
    return node;
  }

  function render() {
    els.rows.querySelectorAll(".cart-row").forEach(function (n) { n.remove(); });
    cart.forEach(function (row) { els.rows.appendChild(rowNode(row)); });

    els.empty.hidden = cart.length > 0;
    els.count.textContent = cart.length + " ta qator";

    var sub = subtotal();
    var auto = autoDiscount();
    var sum = total();

    els.subtotal.textContent = fmt(sub);
    els.autoRow.hidden = auto <= 0;
    els.autoValue.textContent = "−" + fmt(auto);
    if (els.vat) els.vat.textContent = fmt(sum * VAT / (100 + VAT));
    els.total.textContent = fmt(sum) + " " + CURRENCY;

    els.checkout.disabled = cart.length === 0;
    updateChange();
    save();
  }

  function updateChange() {
    var cash = payment === PAYMENT.NAQD;
    els.cashRow.hidden = !cash;
    if (!cash) { els.change.textContent = ""; return; }

    var paid = parseFloat(els.paid.value);
    if (!paid) { els.change.textContent = ""; return; }
    var diff = paid - total();
    els.change.textContent = diff >= 0
      ? "Qaytim: " + fmt(diff) + " " + CURRENCY
      : "Yetmayapti: " + fmt(-diff) + " " + CURRENCY;
    els.change.classList.toggle("shortfall", diff < 0);
  }

  /* --- Qidiruv --------------------------------------------------------- */

  function productFromNode(node) {
    return {
      id: parseInt(node.dataset.product, 10),
      name: node.dataset.name,
      price: parseFloat(node.dataset.price),
      stock: parseFloat(node.dataset.stock),
      unit: node.dataset.unit,
    };
  }

  function bindResults() {
    els.results.querySelectorAll(".pos-item").forEach(function (node) {
      node.addEventListener("click", function () { add(productFromNode(node)); });
    });
  }
  bindResults();

  function drawResults(list) {
    els.results.innerHTML = "";
    if (!list.length) {
      els.results.innerHTML = '<p class="muted">Mos mahsulot topilmadi.</p>';
      return;
    }
    list.forEach(function (p) {
      var node = document.createElement("button");
      node.type = "button";
      node.className = "pos-item";
      node.dataset.product = p.id;
      node.dataset.name = p.name;
      node.dataset.price = p.price;
      node.dataset.stock = p.quantity;
      node.dataset.unit = p.unit;
      node.innerHTML =
        '<span class="pos-item-name">' + p.name + '</span>' +
        (p.color ? '<span class="color-tag"><span class="swatch" style="background:' +
          (p.hex || "#94a3b8") + '"></span>' + p.color + '</span>' : "") +
        '<span class="pos-item-foot"><span class="price">' + fmt(p.price) + '</span>' +
        '<span class="stock' + (p.low ? " low" : "") + '">' + fmt(p.quantity) + " " + p.unit + '</span></span>';
      els.results.appendChild(node);
    });
    bindResults();
  }

  var timer = null;
  function search(immediate) {
    var q = els.search.value.trim();
    clearTimeout(timer);
    timer = setTimeout(function () {
      fetch(SEARCH_URL + "?q=" + encodeURIComponent(q), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          // Skaner to'liq shtrix-kodni yubordi — savatga darhol qo'shamiz.
          if (data.exact && immediate && data.results.length === 1) {
            var p = data.results[0];
            add({ id: p.id, name: p.name, price: p.price, stock: p.quantity, unit: p.unit });
            els.search.value = "";
            els.search.select();
            search(false);
            return;
          }
          drawResults(data.results);
        })
        .catch(function () { showError("Qidiruvda xatolik. Internetni tekshiring."); });
    }, immediate ? 0 : 220);
  }

  els.search.addEventListener("input", function () { search(false); });
  els.search.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    search(true);
  });

  /* --- To'lov turi ----------------------------------------------------- */

  els.payment.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-payment]");
    if (!btn) return;
    payment = parseInt(btn.dataset.payment, 10);
    els.payment.querySelectorAll("[data-payment]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b === btn));
    });
    if (payment === PAYMENT.QARZ && !els.customer.value) {
      showError("Qarzga sotish uchun mijozni tanlang.");
    } else {
      showError("");
    }
    updateChange();
  });

  els.discount.addEventListener("input", render);
  els.paid.addEventListener("input", updateChange);
  els.customer.addEventListener("change", function () {
    showError("");
    render();
  });

  els.clear.addEventListener("click", function () {
    cart = [];
    els.discount.value = "0";
    els.paid.value = "";
    showError("");
    render();
  });

  /* --- Yakunlash ------------------------------------------------------- */

  function finish() {
    if (!cart.length) return;
    els.checkout.disabled = true;
    showError("");

    fetch(CHECKOUT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({
        items: cart.map(function (row) {
          return { product: row.id, quantity: row.quantity, price: row.price };
        }),
        payment_method: payment,
        paid_amount: payment === PAYMENT.NAQD ? (els.paid.value || null) : null,
        discount_amount: manualDiscount(),
        customer: els.customer.value || null,
      }),
    })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok || !result.data.ok) {
          showError(result.data.error || "Sotuvni yakunlab bo'lmadi.");
          els.checkout.disabled = false;
          return;
        }
        cart = [];
        save();
        showDone(result.data);
      })
      .catch(function () {
        showError("Server bilan bog'lanib bo'lmadi.");
        els.checkout.disabled = false;
      });
  }

  function showDone(data) {
    var meta = ["Chek №" + data.number, data.payment];
    if (data.customer) meta.push(data.customer);
    els.doneMeta.textContent = meta.join(" · ");

    els.doneTotal.textContent = fmt(data.total) + " " + CURRENCY;

    // Qaytim faqat naqd to'lovda bo'ladi — kassir shu summani qaytaradi.
    els.doneChangeRow.hidden = !data.change;
    els.doneChange.textContent = fmt(data.change) + " " + CURRENCY;

    els.doneDebtRow.hidden = data.debt == null;
    els.doneDebt.textContent = fmt(data.debt || 0) + " " + CURRENCY;

    els.doneReceipt.href = data.receipt_url + "?chop=1";
    els.done.hidden = false;
    els.doneNext.focus();
  }

  function newSale() {
    els.done.hidden = true;
    els.discount.value = "0";
    els.paid.value = "";
    els.customer.value = "";
    if (els.customer.tomselect) els.customer.tomselect.clear(true);
    els.search.value = "";
    render();
    search(false);
    els.search.focus();
  }

  els.checkout.addEventListener("click", finish);
  els.doneNext.addEventListener("click", newSale);
  els.doneClose.addEventListener("click", newSale);
  els.done.addEventListener("click", function (e) {
    if (e.target === els.done) newSale();       // fon bosilsa ham yopiladi
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "F2") { e.preventDefault(); finish(); }
    if (e.key === "F4") { e.preventDefault(); els.search.focus(); els.search.select(); }
    if (e.key === "Escape" && !els.done.hidden) newSale();
  });

  render();
})();
