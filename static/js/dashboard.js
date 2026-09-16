/* Dashboard diagrammalari (Chart.js).
   Ranglar CSS o'zgaruvchilaridan olinadi — shuning uchun rang rejimi
   almashganda diagrammalar qaytadan chiziladi. */
(function () {
  "use strict";

  var node = document.getElementById("dashboard-data");
  if (!node || typeof Chart === "undefined") return;

  var D = JSON.parse(node.textContent);
  var charts = [];

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // Minglik ajratgich — KPI kartalaridagi formatga mos (bo'shliq).
  function fmt(v) {
    return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
  }

  function palette() {
    return {
      s1: css("--series-1"),
      s2: css("--series-2"),
      s3: css("--series-3"),
      grid: css("--grid"),
      text2: css("--text-2"),
      text3: css("--text-3"),
      surface: css("--surface"),
    };
  }

  function baseOptions(p) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          display: false,
          labels: { color: p.text2, usePointStyle: true, boxWidth: 8, padding: 16 },
        },
        tooltip: {
          backgroundColor: p.surface,
          titleColor: css("--text"),
          bodyColor: p.text2,
          borderColor: css("--border"),
          borderWidth: 1,
          padding: 10,
          cornerRadius: 8,
          displayColors: true,
          usePointStyle: true,
          callbacks: {
            label: function (ctx) {
              return " " + ctx.dataset.label + ": " + fmt(ctx.parsed.y != null ? ctx.parsed.y : ctx.parsed);
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: p.grid },
          ticks: { color: p.text3, font: { size: 11 } },
        },
        y: {
          grid: { color: p.grid, drawTicks: false },
          border: { display: false },
          ticks: {
            color: p.text3,
            font: { size: 11 },
            padding: 8,
            callback: function (v) { return v >= 1e6 ? (v / 1e6) + " mln" : fmt(v); },
          },
        },
      },
    };
  }

  function buildSales(p) {
    return new Chart(document.getElementById("salesChart"), {
      type: "line",
      data: {
        labels: D.days,
        datasets: [
          {
            label: "Tushum",
            data: D.revenue,
            borderColor: p.s1,
            backgroundColor: "transparent",
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBorderWidth: 2,
            pointHoverBorderColor: p.surface,
            pointHoverBackgroundColor: p.s1,
          },
          {
            label: "Foyda",
            data: D.profit,
            borderColor: p.s2,
            backgroundColor: "transparent",
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBorderWidth: 2,
            pointHoverBorderColor: p.surface,
            pointHoverBackgroundColor: p.s2,
          },
        ],
      },
      options: (function () {
        var o = baseOptions(p);
        o.plugins.legend.display = true;   // ikki qatordan iborat — legenda shart
        o.plugins.legend.position = "top";
        o.plugins.legend.align = "end";
        return o;
      })(),
    });
  }

  function buildPayments(p) {
    return new Chart(document.getElementById("paymentsChart"), {
      type: "doughnut",
      data: {
        labels: D.payments.map(function (x) { return x.label; }),
        datasets: [{
          data: D.payments.map(function (x) { return x.value; }),
          backgroundColor: [p.s1, p.s2, p.s3],
          borderColor: p.surface,
          borderWidth: 2,              // bo'laklar orasida 2px bo'shliq
          hoverOffset: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: { display: false },   // qiymatli legenda HTML'da berilgan
          tooltip: {
            backgroundColor: p.surface,
            titleColor: css("--text"),
            bodyColor: p.text2,
            borderColor: css("--border"),
            borderWidth: 1,
            padding: 10,
            cornerRadius: 8,
            callbacks: {
              label: function (ctx) { return " " + ctx.label + ": " + fmt(ctx.parsed); },
            },
          },
        },
      },
    });
  }

  function buildHourly(p) {
    var o = baseOptions(p);
    o.plugins.tooltip.callbacks.label = function (ctx) { return " " + fmt(ctx.parsed.y); };
    return new Chart(document.getElementById("hourlyChart"), {
      type: "bar",
      data: {
        labels: D.hours,
        datasets: [{
          label: "Tushum",
          data: D.hourly,
          backgroundColor: p.s1,
          borderRadius: 4,
          borderSkipped: "bottom",
          maxBarThickness: 22,
        }],
      },
      options: o,
    });
  }

  function buildTop(p) {
    var o = baseOptions(p);
    o.indexAxis = "y";
    o.interaction = { mode: "nearest", intersect: true };
    o.scales.x.grid = { color: p.grid, drawTicks: false };
    o.scales.x.ticks.callback = function (v) { return fmt(v); };
    o.scales.y.grid = { display: false };
    delete o.scales.y.ticks.callback;   // kategoriya o'qi — nomlar o'zi chiqadi
    o.scales.y.ticks.padding = 4;
    o.plugins.tooltip.callbacks.label = function (ctx) { return " " + fmt(ctx.parsed.x) + " dona"; };
    return new Chart(document.getElementById("topChart"), {
      type: "bar",
      data: {
        labels: D.top_products.map(function (x) { return x.name; }),
        datasets: [{
          label: "Sotilgan",
          data: D.top_products.map(function (x) { return x.qty; }),
          backgroundColor: p.s1,
          borderRadius: 4,
          borderSkipped: "left",
          maxBarThickness: 18,
        }],
      },
      options: o,
    });
  }

  function render() {
    charts.forEach(function (c) { c.destroy(); });
    var p = palette();
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    charts = [buildSales(p), buildPayments(p), buildHourly(p), buildTop(p)];
  }

  render();

  // Rejim almashganda CSS o'zgaruvchilari yangilanib ulguriishi uchun kichik kechikish.
  document.addEventListener("pos:themechange", function () {
    setTimeout(render, 30);
  });
  document.addEventListener("pos:resize", function () {
    charts.forEach(function (c) { c.resize(); });
  });
})();
