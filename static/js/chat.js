/* AI yordamchi: suhbat oynasi.
   Xabarlar bazada saqlanadi — sahifa ochilganda server tayyor tarixni beradi.
   Brauzerda hech narsa saqlanmaydi. */
(function () {
  "use strict";

  var config = document.getElementById("chat-config");
  if (!config) return;

  var SEND_URL = config.dataset.send;
  var conversationId = config.dataset.conversation || null;

  var scroll = document.getElementById("chatScroll");
  var thread = document.getElementById("chatThread");
  var hero = document.getElementById("chatHero");
  var input = document.getElementById("chatInput");
  var sendBtn = document.getElementById("chatSend");
  var fileInput = document.getElementById("chatFile");
  var attachBtn = document.getElementById("chatAttach");
  var attachChip = document.getElementById("attachChip");
  var attachName = document.getElementById("attachName");
  var cmdMenu = document.getElementById("cmdMenu");
  var composer = document.getElementById("composerBox");

  var busy = false;
  var attached = null;   // biriktirilgan fayl (File)

  function csrf() {
    var match = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    return match ? decodeURIComponent(match[1]) : "";
  }

  // --- Matnni formatlash ---------------------------------------------------

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* Modeldan kelgan matn HTML emas — avval xavfsiz holga keltiramiz, keyin
     oddiy markdown belgilarini formatlaymiz. */
  function inline(text) {
    return text
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/^[ \t]*[*-][ \t]+/, "• ");
  }

  function isTableRow(line) { return /^\s*\|.*\|\s*$/.test(line); }

  function isSeparator(line) {
    return /^\s*\|[\s:|-]+\|\s*$/.test(line) && line.indexOf("-") !== -1;
  }

  function splitRow(line) {
    return line.trim().replace(/^\||\|$/g, "").split("|").map(function (cell) {
      return inline(cell.trim());
    });
  }

  /* Model ko'pincha javobni markdown jadval qilib yozadi — uni haqiqiy
     jadvalga aylantiramiz. */
  function renderTable(head, rows) {
    var html = '<table class="md-table"><thead><tr>';
    head.forEach(function (cell) { html += "<th>" + cell + "</th>"; });
    html += "</tr></thead><tbody>";
    rows.forEach(function (row) {
      html += "<tr>";
      row.forEach(function (cell) { html += "<td>" + cell + "</td>"; });
      html += "</tr>";
    });
    return html + "</tbody></table>";
  }

  function format(text) {
    var lines = escapeHtml(text).split("\n");
    var out = [];
    var i = 0;

    while (i < lines.length) {
      if (isTableRow(lines[i]) && i + 1 < lines.length && isSeparator(lines[i + 1])) {
        var head = splitRow(lines[i]);
        i += 2;
        var rows = [];
        while (i < lines.length && isTableRow(lines[i])) {
          rows.push(splitRow(lines[i]));
          i++;
        }
        out.push(renderTable(head, rows));
      } else {
        out.push(inline(lines[i]));
        i++;
      }
    }
    return out.join("\n");
  }

  // --- Xabar elementlari ---------------------------------------------------

  /* Pandas natijasi — katta jadval. Uni model takrorlamaydi, bu yerda chiziladi. */
  function buildTable(data) {
    var box = document.createElement("div");
    box.className = "result-table";

    if (data.sarlavha) {
      var caption = document.createElement("div");
      caption.className = "table-caption";
      caption.textContent = data.sarlavha;
      box.appendChild(caption);
    }

    var wrap = document.createElement("div");
    wrap.className = "table-scroll";

    var table = document.createElement("table");
    var head = document.createElement("thead");
    var headRow = document.createElement("tr");
    (data.ustunlar || []).forEach(function (name) {
      var th = document.createElement("th");
      th.textContent = name;
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var body = document.createElement("tbody");
    (data.qatorlar || []).forEach(function (row) {
      var tr = document.createElement("tr");
      row.forEach(function (cell) {
        var td = document.createElement("td");
        if (cell === null) td.textContent = "—";
        else if (typeof cell === "number") {
          // Sonlar xonalarga ajratiladi, ortiqcha ".0" tashlanadi.
          td.textContent = spaced(Number.isInteger(cell) ? cell : +cell.toFixed(3));
          td.className = "num";
        } else td.textContent = String(cell);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    box.appendChild(wrap);

    if (data.qisqartirilgan) {
      var note = document.createElement("div");
      note.className = "table-note";
      note.textContent = "Jami " + data.jami + " qatordan birinchilari ko'rsatildi.";
      box.appendChild(note);
    }

    // Nakladnoy qoralamasi — tasdiqlash sahifasiga havola.
    if (data.havola) {
      var link = document.createElement("a");
      link.className = "table-link";
      link.href = data.havola;
      link.textContent = data.havola_matni || "Ochish →";
      box.appendChild(link);
    }
    return box;
  }

  function spaced(number) {
    return String(number).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  /* Javob ostidagi o'lchovlar: qancha token ketdi va necha soniya oldi. */
  function buildStats(stats) {
    var line = document.createElement("div");
    line.className = "msg-stats";

    var tokens = spaced(stats.tokens) + " token";
    // Kirish/chiqish faqat ikkalasi ma'lum bo'lsa ko'rsatiladi.
    if (stats.output) tokens += " (" + spaced(stats.input) + " → " + spaced(stats.output) + ")";
    var parts = [tokens, stats.seconds + " s"];
    if (stats.tools) parts.push(stats.tools + " ta asbob");

    line.textContent = parts.join(" · ");
    return line;
  }

  function addMessage(item) {
    if (hero) hero.hidden = true;

    var row = document.createElement("div");
    row.className = "msg " + item.role;

    if (item.role === "ai" || item.role === "error") {
      var mark = document.createElement("span");
      mark.className = "msg-mark";
      mark.textContent = "AP";
      row.appendChild(mark);
    }

    var column = document.createElement("div");
    column.className = "msg-col";

    var bubble = document.createElement("div");
    bubble.className = "bubble";
    if (item.role === "ai") bubble.innerHTML = format(item.text);
    else bubble.textContent = item.text;
    column.appendChild(bubble);

    if (item.role === "ai" && item.tables && item.tables.length) {
      item.tables.forEach(function (data) { column.appendChild(buildTable(data)); });
    }

    // Model qaysi ma'lumotdan foydalangani — javobni tekshirish imkonini beradi.
    if (item.role === "ai" && item.sources && item.sources.length) {
      var list = document.createElement("div");
      list.className = "sources";
      item.sources.forEach(function (source) {
        var chip = document.createElement("span");
        chip.className = "source";
        chip.textContent = source;
        list.appendChild(chip);
      });
      column.appendChild(list);
    }

    if (item.role === "ai" && item.stats) column.appendChild(buildStats(item.stats));

    row.appendChild(column);
    thread.appendChild(row);
    toBottom();
    return row;
  }

  function addTyping() {
    var row = document.createElement("div");
    row.className = "msg ai";
    row.innerHTML = '<span class="msg-mark">AP</span>' +
                    '<div class="dots"><i></i><i></i><i></i></div>';
    thread.appendChild(row);
    toBottom();
    return row;
  }

  function toBottom() { scroll.scrollTop = scroll.scrollHeight; }

  // --- Yuborish ------------------------------------------------------------

  /* Birinchi savoldan keyin suhbat serverda yaratiladi: manzilni va ro'yxatni
     sahifani yangilamasdan yangilaymiz. */
  function registerConversation(data) {
    if (conversationId || !data.conversation) return;
    conversationId = String(data.conversation);
    config.dataset.conversation = conversationId;
    window.history.replaceState({}, "", data.url);

    var items = document.querySelector(".conv-items");
    if (!items) return;
    var empty = items.querySelector(".conv-empty");
    if (empty) empty.remove();

    var box = document.createElement("div");
    box.className = "conv active";
    box.innerHTML = '<a class="conv-link" href="' + data.url + '">' +
      '<span class="conv-title"></span><span class="conv-date">hozir</span></a>';
    box.querySelector(".conv-title").textContent = data.title || "Suhbat";
    items.insertBefore(box, items.firstChild);
  }

  function request(text) {
    if (attached) {
      // Fayl bilan — multipart. Content-Type ni brauzer o'zi qo'yadi (boundary bilan).
      var body = new FormData();
      body.append("message", text);
      if (conversationId) body.append("conversation", conversationId);
      body.append("file", attached);
      return fetch(SEND_URL, { method: "POST", headers: { "X-CSRFToken": csrf() }, body: body });
    }
    return fetch(SEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ message: text, conversation: conversationId })
    });
  }

  function send() {
    var text = (input.value || "").trim();
    if ((!text && !attached) || busy) return;

    busy = true;
    sendBtn.disabled = true;
    input.value = "";
    resize();
    hideMenu();

    // Ekranda aynan yuborilgan narsa ko'rinsin.
    addMessage({ role: "user", text: attached ? (text + " 📎 " + attached.name).trim() : text });
    var typing = addTyping();
    var pending = request(text);
    clearFile();

    pending
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        typing.remove();
        registerConversation(result.data);
        if (result.ok && result.data.reply) {
          addMessage({
            role: "ai",
            text: result.data.reply,
            sources: result.data.sources,
            tables: result.data.tables,
            stats: result.data.stats
          });
        } else {
          addMessage({ role: "error", text: result.data.error || "Javob olinmadi." });
        }
      })
      .catch(function () {
        typing.remove();
        addMessage({ role: "error", text: "Serverga ulanib bo'lmadi. Internetni tekshiring." });
      })
      .then(function () {
        busy = false;
        sendBtn.disabled = false;
        input.focus();
      });
  }

  // --- Kiritish maydoni ----------------------------------------------------

  function resize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }

  // --- Fayl biriktirish ----------------------------------------------------

  function setFile(file) {
    if (!file) return;
    attached = file;
    attachName.textContent = file.name + " · " + Math.ceil(file.size / 1024) + " KB";
    attachChip.hidden = false;
    // Buyruq yozilmagan bo'lsa — eng ko'p ishlatiladiganini taklif qilamiz.
    if (!input.value.trim()) input.value = "/qabul ";
    input.focus();
  }

  function clearFile() {
    attached = null;
    fileInput.value = "";
    attachChip.hidden = true;
  }

  attachBtn.addEventListener("click", function () { fileInput.click(); });
  fileInput.addEventListener("change", function () { setFile(fileInput.files[0]); });
  document.getElementById("attachRemove").addEventListener("click", clearFile);

  ["dragenter", "dragover"].forEach(function (name) {
    composer.addEventListener(name, function (event) {
      event.preventDefault();
      composer.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach(function (name) {
    composer.addEventListener(name, function () { composer.classList.remove("dragging"); });
  });
  composer.addEventListener("drop", function (event) {
    event.preventDefault();
    if (event.dataTransfer.files.length) setFile(event.dataTransfer.files[0]);
  });

  // --- Buyruqlar menyusi ---------------------------------------------------

  function hideMenu() { cmdMenu.hidden = true; }

  function updateMenu() {
    var value = input.value;
    cmdMenu.hidden = !(value.charAt(0) === "/" && value.indexOf(" ") === -1);
  }

  Array.prototype.forEach.call(cmdMenu.querySelectorAll("[data-cmd]"), function (button) {
    button.addEventListener("click", function () {
      input.value = button.dataset.cmd + " ";
      hideMenu();
      input.focus();
      if (!attached) fileInput.click();
    });
  });

  input.addEventListener("input", resize);
  input.addEventListener("input", updateMenu);
  input.addEventListener("keydown", function (event) {
    // Enter — yuborish, Shift+Enter — yangi qator.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  sendBtn.addEventListener("click", send);

  Array.prototype.forEach.call(document.querySelectorAll(".suggest"), function (button) {
    button.addEventListener("click", function () {
      input.value = button.textContent.trim();
      resize();
      send();
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll(".conv-form"), function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm("Suhbat o'chirilsinmi?")) event.preventDefault();
    });
  });

  // --- Sahifa ochilganda ---------------------------------------------------

  (function restore() {
    var node = document.getElementById("chat-history");
    var history = [];
    try { history = JSON.parse(node.textContent); } catch (e) { history = []; }
    history.forEach(addMessage);
    if (!input.disabled) input.focus();
  })();
})();
