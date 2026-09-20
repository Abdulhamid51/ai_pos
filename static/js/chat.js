/* AI yordamchi: suhbat oynasi.
   Xabarlar ro'yxati brauzerda (sessionStorage) saqlanadi — sahifa yangilansa
   ko'rinish yo'qolmaydi. Suhbat tarixini model tomoni eslaydi; server esa
   faqat oxirgi javob id'sini sessiyada yuritadi. */
(function () {
  "use strict";

  var config = document.getElementById("chat-config");
  if (!config) return;

  var SEND_URL = config.dataset.send;
  var RESET_URL = config.dataset.reset;
  var STORE_KEY = "ai-chat";

  var scroll = document.getElementById("chatScroll");
  var thread = document.getElementById("chatThread");
  var hero = document.getElementById("chatHero");
  var input = document.getElementById("chatInput");
  var sendBtn = document.getElementById("chatSend");
  var newBtn = document.getElementById("chatNew");

  var busy = false;

  function csrf() {
    var match = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    return match ? decodeURIComponent(match[1]) : "";
  }

  // --- Saqlash -------------------------------------------------------------

  function load() {
    try { return JSON.parse(sessionStorage.getItem(STORE_KEY)) || []; }
    catch (e) { return []; }
  }

  function save(items) {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(items)); } catch (e) {}
  }

  function remember(role, text, sources, tables) {
    var items = load();
    items.push({ role: role, text: text, sources: sources || [], tables: tables || [] });
    save(items);
  }

  // --- Chizish -------------------------------------------------------------

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
      // Qator boshidagi "* " yoki "- " — ro'yxat belgisi, nuqtaga aylantiramiz.
      .replace(/^[ \t]*[*-][ \t]+/, "• ");
  }

  function isTableRow(line) {
    return /^\s*\|.*\|\s*$/.test(line);
  }

  function isSeparator(line) {
    return /^\s*\|[\s:|-]+\|\s*$/.test(line) && line.indexOf("-") !== -1;
  }

  function splitRow(line) {
    return line.trim().replace(/^\||\|$/g, "").split("|").map(function (cell) {
      return inline(cell.trim());
    });
  }

  /* Model ko'pincha javobni markdown jadval qilib yozadi. Uni xom ko'rsatish
     o'rniga haqiqiy jadvalga aylantiramiz. */
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

  function addMessage(role, text, sources, tables) {
    if (hero) hero.hidden = true;

    var row = document.createElement("div");
    row.className = "msg " + role;

    if (role === "ai" || role === "error") {
      var mark = document.createElement("span");
      mark.className = "msg-mark";
      mark.textContent = "AP";
      row.appendChild(mark);
    }

    var column = document.createElement("div");
    column.className = "msg-col";

    var bubble = document.createElement("div");
    bubble.className = "bubble";
    if (role === "ai") bubble.innerHTML = format(text);
    else bubble.textContent = text;
    column.appendChild(bubble);

    if (role === "ai" && tables && tables.length) {
      tables.forEach(function (data) { column.appendChild(buildTable(data)); });
    }

    // Model qaysi ma'lumotdan foydalangani — javobni tekshirish imkonini beradi.
    if (role === "ai" && sources && sources.length) {
      var list = document.createElement("div");
      list.className = "sources";
      sources.forEach(function (source) {
        var chip = document.createElement("span");
        chip.className = "source";
        chip.textContent = source;
        list.appendChild(chip);
      });
      column.appendChild(list);
    }

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

  function toBottom() {
    scroll.scrollTop = scroll.scrollHeight;
  }

  // --- Yuborish ------------------------------------------------------------

  function send() {
    var text = (input.value || "").trim();
    if (!text || busy) return;

    busy = true;
    sendBtn.disabled = true;
    input.value = "";
    resize();

    addMessage("user", text);
    remember("user", text);

    var typing = addTyping();

    fetch(SEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ message: text })
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        typing.remove();
        if (result.ok && result.data.reply) {
          addMessage("ai", result.data.reply, result.data.sources, result.data.tables);
          remember("ai", result.data.reply, result.data.sources, result.data.tables);
        } else {
          addMessage("error", result.data.error || "Javob olinmadi.");
        }
      })
      .catch(function () {
        typing.remove();
        addMessage("error", "Serverga ulanib bo'lmadi. Internetni tekshiring.");
      })
      .then(function () {
        busy = false;
        sendBtn.disabled = false;
        input.focus();
      });
  }

  function reset() {
    fetch(RESET_URL, { method: "POST", headers: { "X-CSRFToken": csrf() } })
      .catch(function () {})
      .then(function () {
        save([]);
        thread.innerHTML = "";
        if (hero) { thread.appendChild(hero); hero.hidden = false; }
        input.focus();
      });
  }

  // --- Kiritish maydoni ----------------------------------------------------

  function resize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }

  input.addEventListener("input", resize);
  input.addEventListener("keydown", function (event) {
    // Enter — yuborish, Shift+Enter — yangi qator.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  sendBtn.addEventListener("click", send);
  if (newBtn) newBtn.addEventListener("click", reset);

  // Taklif tugmalari
  Array.prototype.forEach.call(document.querySelectorAll(".suggest"), function (button) {
    button.addEventListener("click", function () {
      input.value = button.textContent.trim();
      resize();
      send();
    });
  });

  // --- Sahifa ochilganda ---------------------------------------------------

  (function restore() {
    var items = load();
    items.forEach(function (item) {
      addMessage(item.role, item.text, item.sources, item.tables);
    });
    if (!items.length && hero) hero.hidden = false;
    if (!input.disabled) input.focus();
  })();
})();
