/* AI POS — interfeys skriptlari: rang rejimi, yon menyu, sozlamalar modali. */
(function () {
  "use strict";

  var THEME_KEY = "pos-theme";
  var SIDEBAR_KEY = "pos-sidebar";
  var root = document.documentElement;

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }
  function read(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }

  /* --- Rang rejimi --------------------------------------------------- */

  function applyTheme(mode) {
    if (mode === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", mode);
    }
    document.querySelectorAll("#themeSwitch [data-theme-value]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.themeValue === mode));
    });
    // Diagrammalar ranglarni CSS'dan oladi — rejim o'zgarganda qayta chiziladi.
    document.dispatchEvent(new CustomEvent("pos:themechange", { detail: { mode: mode } }));
  }

  var currentTheme = read(THEME_KEY, "system");
  applyTheme(currentTheme);

  var themeSwitch = document.getElementById("themeSwitch");
  if (themeSwitch) {
    themeSwitch.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-theme-value]");
      if (!btn) return;
      currentTheme = btn.dataset.themeValue;
      store(THEME_KEY, currentTheme);
      applyTheme(currentTheme);
    });
  }

  // "Tizim" rejimida OS sozlamasi o'zgarsa, diagrammalar ham yangilansin.
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onSystemChange = function () {
      if (currentTheme === "system") applyTheme("system");
    };
    if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
    else if (mq.addListener) mq.addListener(onSystemChange);
  }

  /* --- Yon menyuni yig'ish ------------------------------------------- */

  var body = document.body;
  if (root.classList.contains("pre-collapsed")) {
    body.classList.add("sidebar-collapsed");
    root.classList.remove("pre-collapsed");
  }

  var toggle = document.getElementById("sidebarToggle");
  var sidebarEl = document.querySelector(".sidebar");

  function announceResize() {
    document.dispatchEvent(new CustomEvent("pos:resize"));
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      var collapsed = body.classList.toggle("sidebar-collapsed");
      store(SIDEBAR_KEY, collapsed ? "collapsed" : "expanded");

      // Diagrammalar menyuning kengligi o'zgarib BO'LGANDAN keyin qayta o'lchanadi,
      // aks holda eski kenglik bilan qolib, kartani cho'zib yuboradi.
      if (sidebarEl) {
        sidebarEl.addEventListener("transitionend", function onEnd(e) {
          if (e.propertyName !== "width") return;
          sidebarEl.removeEventListener("transitionend", onEnd);
          announceResize();
        });
      }
      setTimeout(announceResize, 260);   // transitionend kelmasa — zaxira
    });
  }

  /* --- Qidiruvli select'lar (Tom Select) ------------------------------ */

  function enhanceSelects(scope) {
    if (typeof TomSelect === "undefined") return;
    (scope || document).querySelectorAll("select").forEach(function (el) {
      if (el.tomselect || el.dataset.noSearch === "1") return;
      var multi = el.multiple;
      var emptyOption = el.querySelector('option[value=""]');
      var emptyText = emptyOption ? emptyOption.textContent.trim() : "";
      // Bo'sh variant ko'rsatma matni bo'lsa (majburiy maydon yoki Django'ning
      // "---------" belgisi) — uni qiymat sifatida ko'rsatmaymiz, placeholder qilamiz.
      var emptyIsPlaceholder = !multi && emptyOption && (el.required || /^[-\s]*$/.test(emptyText));

      var plugins = [];
      if (multi) plugins.push("remove_button");
      // Ixtiyoriy maydonda tanlovni bekor qilish uchun tozalash tugmasi.
      else if (emptyIsPlaceholder && !el.required) plugins.push("clear_button");

      new TomSelect(el, {
        plugins: plugins,
        maxOptions: null,
        allowEmptyOption: !emptyIsPlaceholder,
        placeholder: multi ? "Tanlang" : (emptyIsPlaceholder && emptyText.replace(/[-\s]/g, "")
                                          ? emptyText : (emptyIsPlaceholder ? "Tanlang" : emptyText || null)),
        render: {
          no_results: function () { return '<div class="no-results">Topilmadi</div>'; }
        }
      });
    });
  }

  enhanceSelects(document);
  document.addEventListener("pos:selects", function (e) {
    enhanceSelects(e.detail && e.detail.scope);
  });

  /* --- Sozlamalar modali --------------------------------------------- */

  var modal = document.getElementById("settingsModal");
  var openBtn = document.getElementById("settingsOpen");
  var closeBtn = document.getElementById("settingsClose");

  function showPanel(name) {
    if (!modal) return;
    var item = modal.querySelector('.side-item[data-panel="' + name + '"]');
    if (!item) return;
    modal.querySelectorAll(".side-item").forEach(function (i) { i.classList.remove("active"); });
    item.classList.add("active");
    modal.querySelectorAll("[data-panel-body]").forEach(function (section) {
      section.hidden = section.dataset.panelBody !== name;
    });
  }

  function openModal(panel) {
    if (!modal) return;
    modal.hidden = false;
    if (panel) showPanel(panel);
    var box = modal.querySelector(".modal");
    if (box) box.focus();
  }
  function closeModal() {
    if (modal) modal.hidden = true;
  }

  if (openBtn) openBtn.addEventListener("click", function () { openModal(); });

  // Yon menyudagi "Filiallar" kabi havolalar to'g'ridan-to'g'ri panelni ochadi.
  document.querySelectorAll("[data-open-settings]").forEach(function (link) {
    link.addEventListener("click", function (e) {
      e.preventDefault();
      openModal(link.dataset.openSettings);
    });
  });

  // Forma yuborilgandan keyin sahifa #settings/<panel> bilan qaytadi.
  (function () {
    var match = /^#settings\/(\w+)$/.exec(window.location.hash);
    if (match) openModal(match[1]);
  })();
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (modal) {
    modal.addEventListener("click", function (e) {
      if (e.target === modal) closeModal();          // fon bosilsa yopiladi
    });
    modal.querySelectorAll(".side-item[data-panel]").forEach(function (item) {
      item.addEventListener("click", function () { showPanel(item.dataset.panel); });
    });

    // Sozlamalar ichidagi qidiruv — bo'limlar ro'yxatini filtrlaydi.
    var search = document.getElementById("settingsSearch");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        modal.querySelectorAll(".side-item[data-panel]").forEach(function (item) {
          item.hidden = q && item.textContent.toLowerCase().indexOf(q) === -1;
        });
      });
    }

    // Filialni tahrirlash — jadvaldagi tugma formani to'ldiradi.
    var branchForm = document.getElementById("branchForm");
    if (branchForm) {
      var title = document.getElementById("branchFormTitle");
      var reset = document.getElementById("branchFormReset");
      var baseAction = branchForm.getAttribute("action");

      modal.querySelectorAll(".branch-edit").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var d = btn.dataset;
          branchForm.setAttribute("action", baseAction + d.pk + "/");
          title.textContent = d.name + " — tahrirlash";
          branchForm.querySelector("#bf-name").value = d.name;
          branchForm.querySelector("#bf-address").value = d.address;
          branchForm.querySelector("#bf-phone").value = d.phone;
          branchForm.querySelector("#bf-main").checked = d.main === "1";
          branchForm.querySelector("#bf-active").checked = d.active === "1";
          var trade = branchForm.querySelector("#bf-trade");
          if (trade.tomselect) trade.tomselect.setValue(d.trade);
          else trade.value = d.trade;
          reset.hidden = false;
          branchForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
      });

      if (reset) {
        reset.addEventListener("click", function () {
          branchForm.reset();
          branchForm.setAttribute("action", baseAction);
          title.textContent = "Yangi filial";
          reset.hidden = true;
        });
      }
    }
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal && !modal.hidden) closeModal();
  });
})();
