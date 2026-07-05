// palette.js — command palette (⌘K / Ctrl K). Framework-free progressive
// enhancement: every destination here is a normal route reachable without it.
// The index comes from GET /palette.json; action rows reuse window.alUI (app.js).
(() => {
  "use strict";

  // category key in the JSON -> short tag shown on each row, in display order
  const CATS = [["views", "Go"], ["lists", "List"], ["habits", "Habit"],
                ["lessons", "Learn"], ["actions", "Do"]];

  let cache = null, cacheAt = 0, rows = [], sel = 0, open = false;
  let overlay, input, listEl;

  // subsequence fuzzy score: every query char must appear in order; reward
  // contiguous runs and word-start hits, mildly prefer shorter labels.
  function score(q, text) {
    if (!q) return 0;
    const t = text.toLowerCase();
    let ti = 0, s = 0, prev = -2;
    for (const c of q) {
      let f = -1;
      for (let j = ti; j < t.length; j++) { if (t[j] === c) { f = j; break; } }
      if (f < 0) return -1;
      s += (f === prev + 1) ? 3 : 1;
      if (f === 0 || /[\s/·|-]/.test(t[f - 1])) s += 2;
      prev = f; ti = f + 1;
    }
    return s - t.length * 0.01;
  }

  async function load() {
    const now = Date.now();
    if (cache && now - cacheAt < 30000) return cache;
    try {
      const r = await fetch("/palette.json", { headers: { "X-Partial": "1" } });
      cache = await r.json();
      cacheAt = now;
    } catch (_) {
      cache = cache || { views: [], lists: [], habits: [], lessons: [], actions: [] };
    }
    return cache;
  }

  function flat(d) {
    const out = [];
    for (const [key, tag] of CATS) {
      for (const it of (d[key] || [])) out.push(Object.assign({ tag }, it));
    }
    return out;
  }

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function build() {
    overlay = document.createElement("div");
    overlay.className = "pal";
    overlay.innerHTML =
      '<div class="pal-box" role="dialog" aria-modal="true" aria-label="Command palette">' +
      '<input class="pal-input" type="text" role="combobox" aria-expanded="true" ' +
      'aria-controls="pal-list" aria-autocomplete="list" spellcheck="false" ' +
      'placeholder="Search views, lists, habits, lessons…" autocomplete="off">' +
      '<ul class="pal-list" id="pal-list" role="listbox"></ul>' +
      '<div class="pal-foot"><span>↑↓ move</span><span>↵ open</span><span>esc close</span></div>' +
      "</div>";
    input = overlay.querySelector(".pal-input");
    listEl = overlay.querySelector(".pal-list");
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    input.addEventListener("input", () => render(input.value.trim()));
    input.addEventListener("keydown", onKey);
    listEl.addEventListener("click", (e) => {
      const li = e.target.closest("[data-i]");
      if (li) activate(rows[+li.dataset.i]);
    });
    document.body.appendChild(overlay);
  }

  function render(q) {
    let list = flat(cache || {});
    if (q) {
      const ql = q.toLowerCase();
      list = list
        .map((it) => ({ it, s: score(ql, it.label + " " + it.tag) }))
        .filter((x) => x.s >= 0)
        .sort((a, b) => b.s - a.s)
        .map((x) => x.it);
    }
    rows = list;
    sel = 0;
    listEl.innerHTML = list.length
      ? list.map((it, i) =>
          '<li class="pal-row" role="option" data-i="' + i + '" id="pal-o-' + i + '">' +
          '<span class="pal-ic" aria-hidden="true">' + esc(it.emoji || "") + "</span>" +
          '<span class="pal-name">' + esc(it.label) + "</span>" +
          '<span class="pal-tag">' + esc(it.tag) + "</span>" +
          (it.hint ? '<span class="pal-kbd">' + esc(it.hint) + "</span>"
            : (it.count ? '<span class="pal-cnt">' + esc(it.count) + "</span>" : "")) +
          "</li>").join("")
      : '<li class="pal-empty">No matches</li>';
    mark();
  }

  function mark() {
    const opts = listEl.querySelectorAll(".pal-row");
    opts.forEach((o, i) => o.classList.toggle("on", i === sel));
    const cur = opts[sel];
    if (cur) {
      cur.scrollIntoView({ block: "nearest" });
      input.setAttribute("aria-activedescendant", cur.id);
    }
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); if (rows.length) { sel = (sel + 1) % rows.length; mark(); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (rows.length) { sel = (sel - 1 + rows.length) % rows.length; mark(); } }
    else if (e.key === "Enter") { e.preventDefault(); if (rows[sel]) activate(rows[sel]); }
  }

  function activate(it) {
    if (!it) return;
    close();
    if (it.href) { window.location.href = it.href; return; }
    const ui = window.alUI || {};
    if (it.shortcut === "n" && ui.newTask) ui.newTask();
    else if (it.shortcut === "t" && ui.toggleTheme) ui.toggleTheme();
    else if (it.shortcut === "?" && ui.showHints) ui.showHints();
  }

  async function openPalette() {
    if (open) return;
    if (!overlay) build();
    open = true;
    overlay.classList.add("show");
    input.value = "";
    render("");                 // instant frame from any warm cache
    input.focus();
    await load();
    if (open) render("");       // refresh once the index lands
  }

  function close() {
    if (!open) return;
    open = false;
    overlay.classList.remove("show");
    input.blur();
  }

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      open ? close() : openPalette();
    }
  });
})();
