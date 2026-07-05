// TickTick-clone — Mode B progressive enhancement.
// Framework-free. The server-rendered forms (Mode A) work without this file; here
// we intercept them to fetch + patch the DOM in place (no full reload). The data
// contract is identical, so disabling JS just falls back to 303-redirect reloads.

(() => {
  "use strict";

  /** POST form-encoded data and parse the JSON partial response. */
  async function postPartial(url, params) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "X-Partial": "1", "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(params).toString(),
      });
      return await r.json();
    } catch (_) {
      return { ok: false, error: "network error" };
    }
  }

  let toastTimer = null;
  // toast(msg) — transient status. toast(msg, {label, fn}) — adds an action
  // button (e.g. Undo) and holds longer so it can be clicked.
  function toast(msg, action) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = "";
    const span = document.createElement("span");
    span.textContent = msg;
    el.appendChild(span);
    const hasAction = action && action.label && typeof action.fn === "function";
    if (hasAction) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "toast-action";
      btn.textContent = action.label;
      btn.addEventListener("click", () => { el.classList.remove("show"); action.fn(); });
      el.appendChild(btn);
    }
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), hasAction ? 5000 : 2200);
  }

  // --- habit check-in (binary): reflect {status,current_streak,total} onto row ---
  function applyCheckin(state) {
    const row = document.getElementById("item-" + state.item_id);
    if (!row) return;
    const done = !!state.status;
    row.dataset.status = state.status || "";
    const ring = row.querySelector("[data-dot]");
    if (ring) ring.classList.toggle("done", done);
    if (typeof state.current_streak === "number") {
      const cur = row.querySelector("[data-streak-cur]");
      if (cur) cur.textContent = state.current_streak;
    }
    if (typeof state.total === "number") {
      const tot = row.querySelector("[data-total]");
      if (tot) tot.textContent = state.total;
    }
  }

  // --- intercept form submits: check-ins, task complete, daily note --------
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute("data-native")) return;  // let Mode A handle it (full reload)
    const action = form.getAttribute("action") || "";

    if (action === "/checkins") {
      e.preventDefault();
      const res = await postPartial("/checkins", Object.fromEntries(new FormData(form)));
      if (res.ok) applyCheckin(res);
      else toast(res.error || "could not save");
    } else if (/^\/tasks\/\d+\/complete$/.test(action)) {
      e.preventDefault();
      const row = form.closest(".trow");
      const res = await postPartial(action, Object.fromEntries(new FormData(form)));
      if (!res.ok) { toast(res.error || "could not save"); return; }
      if (row) row.classList.toggle("done", res.completed);
      const cb = form.querySelector(".checkbox");
      if (cb) cb.classList.toggle("on", res.completed);
      if (res.completed) {
        toast("Task completed", { label: "Undo", fn: async () => {
          const undo = await postPartial(action, Object.fromEntries(new FormData(form)));
          if (undo.ok) {
            if (row) row.classList.toggle("done", undo.completed);
            if (cb) cb.classList.toggle("on", undo.completed);
          }
        } });
      }
    } else if (action === "/daily-note") {
      e.preventDefault();
      const res = await postPartial("/daily-note", Object.fromEntries(new FormData(form)));
      toast(res.ok ? "Daily note saved" : (res.error || "could not save"));
    }
  });

  // --- Learn HTML preview: poll the runtime file mtime and reload the sandboxed iframe.
  (() => {
    const frame = document.getElementById("lesson-preview-frame");
    if (!frame) return;
    const metaUrl = frame.dataset.metaUrl;
    const fallbackSrc = frame.getAttribute("src");
    if (!metaUrl || !fallbackSrc) return;
    let version = frame.dataset.version || "";
    let inFlight = false;
    async function tick() {
      if (document.hidden || inFlight) return;
      inFlight = true;
      try {
        const r = await fetch(metaUrl, { cache: "no-store" });
        const data = await r.json();
        const next = String(data.version || "0");
        if (next !== version) {
          version = next;
          const src = data.preview_url || (data.exists ? frame.dataset.src : fallbackSrc);
          const url = new URL(src, window.location.href);
          url.searchParams.set("_v", Date.now());
          frame.src = url.toString();
        }
      } catch (_) {
        // Preview is best-effort; the next tick will try again.
      } finally {
        inFlight = false;
      }
    }
    setInterval(tick, 1200);
    document.addEventListener("visibilitychange", tick);
  })();

  // --- theme: tri-state (system | light | dark); default follows the OS --------
  // The storage key, resolve rule and system media query live in ONE place:
  // window.alTheme, defined by base.html's pre-paint head script (which always
  // runs before us) — this block only drives the toggle through it.
  (() => {
    const btns = document.querySelectorAll(".theme-toggle");
    const { ORDER, read, save, resolve, mq } = window.alTheme;
    const LABEL = { system: "System", light: "Light", dark: "Dark" };
    function apply(pref) {
      document.documentElement.setAttribute("data-theme", resolve(pref));
      btns.forEach((b) => {
        b.dataset.pref = pref;
        b.title = "Theme: " + LABEL[pref];
        b.setAttribute("aria-label", "Theme: " + LABEL[pref] + " — tap to change");
      });
      save(pref);
    }
    btns.forEach((b) => b.addEventListener("click", () =>
      apply(ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length])));
    // live-react to OS theme changes while in "system" mode
    const onSystemChange = () => { if (read() === "system") apply("system"); };
    if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
    else if (mq.addListener) mq.addListener(onSystemChange);
    apply(read());  // sync data-theme + button UI on load
  })();

  // --- global keyboard shortcuts + "?" cheat sheet ----------------------------
  // Chord nav (g→t/c/f/…), single-key actions, and a help overlay. The palette
  // (palette.js) owns ⌘K and reuses these action handlers via window.alUI.
  (() => {
    const NAV = { t: "/today", c: "/calendar", f: "/focus", m: "/matrix",
                  h: "/habits", l: "/learn", s: "/search" };
    const HINTS = [
      ["⌘K  Ctrl K", "Command palette"],
      ["n", "New task"],
      ["g t / c / f", "Tasks / Calendar / Focus"],
      ["g m / h", "Matrix / Habits"],
      ["g l / s", "Learn / Search"],
      ["t", "Toggle theme"],
      ["?", "This help"],
    ];

    function typing(el) {
      if (!el || el.nodeType !== 1) return false;  // only Elements can be edit targets
      return el.isContentEditable ||
        /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || !!el.closest(".term-drawer");
    }
    function newTask() {
      const qa = document.querySelector(".qa-input");
      if (qa) { qa.focus(); if (qa.select) qa.select(); }
      else window.location.href = "/today";
    }
    function toggleTheme() {
      const b = document.querySelector(".theme-toggle");
      if (b) b.click();
    }
    let hintsEl = null;
    function closeHints() { if (hintsEl) { hintsEl.remove(); hintsEl = null; } }
    function showHints() {
      if (hintsEl) { closeHints(); return; }
      hintsEl = document.createElement("div");
      hintsEl.className = "kbd-hints";
      hintsEl.innerHTML =
        '<div class="kbd-card" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">' +
        "<h2>Keyboard</h2><dl>" +
        HINTS.map((r) => "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>").join("") +
        "</dl></div>";
      hintsEl.addEventListener("mousedown", (e) => { if (e.target === hintsEl) closeHints(); });
      document.body.appendChild(hintsEl);
    }
    window.alUI = { newTask, toggleTheme, showHints, closeHints, toast };

    let armed = false, armTimer = null;
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeHints(); return; }
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (typing(e.target)) return;
      if (armed) {
        armed = false; clearTimeout(armTimer);
        const dest = NAV[e.key.toLowerCase()];
        if (dest) { e.preventDefault(); window.location.href = dest; }
        return;
      }
      if (e.key === "g") { armed = true; armTimer = setTimeout(() => (armed = false), 600); return; }
      if (e.key === "n") { e.preventDefault(); newTask(); }
      else if (e.key === "t") { e.preventDefault(); toggleTheme(); }
      else if (e.key === "?") { e.preventDefault(); showHints(); }
    });
  })();

  // --- Pomodoro / Stopwatch (focus page) --------------------------------------
  (() => {
    const ft = document.getElementById("focus-time");
    const fstart = document.getElementById("focus-start");
    if (!ft || !fstart) return;
    const fend = document.getElementById("focus-end");
    const ring = document.getElementById("focus-ring");
    const POMO = 25 * 60;
    let mode = "pomo", remaining = POMO, elapsed = 0, timer = null, running = false;

    const fmt = (s) => String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    function render() {
      ft.textContent = fmt(mode === "pomo" ? remaining : elapsed);
      if (fend) fend.hidden = !(mode === "stopwatch" && elapsed > 0);
      if (ring) ring.style.setProperty("--focus-progress", String(mode === "pomo" ? (POMO - remaining) / POMO : (elapsed % POMO) / POMO));
    }

    // --- persist a finished session, then patch the Overview + Record list -----
    function applyFocus(ov) {
      if (!ov) return;
      const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
      set("st-today-pomo", String(ov.today_pomo));
      set("st-today-focus", ov.today_focus.value + "<small>" + ov.today_focus.unit + "</small>");
      set("st-total-pomo", String(ov.total_pomo));
      set("st-total-focus", ov.total_focus.value + "<small>" + ov.total_focus.unit + "</small>");
    }
    function prependRecord(rec) {
      const list = document.getElementById("focus-rec-list");
      if (!rec || !list) return;
      const empty = document.getElementById("focus-rec-empty");
      if (empty) empty.remove();
      const li = document.createElement("li");
      li.className = "focus-rec-row";
      li.dataset.id = rec.id;
      // dot class from a server enum (pomo|stopwatch); text via textContent (XSS-safe)
      li.innerHTML =
        '<span class="fr-dot fr-' + (rec.mode === "pomo" ? "pomo" : "stopwatch") + '" aria-hidden="true"></span>' +
        '<span class="fr-main"><span class="fr-dur"></span><span class="fr-sub"></span></span>' +
        '<span class="fr-time"></span>';
      li.querySelector(".fr-dur").textContent = rec.duration_label;
      li.querySelector(".fr-sub").textContent =
        rec.mode_label + (rec.lesson_title ? " · " + rec.lesson_title : "");
      li.querySelector(".fr-time").textContent = rec.time_label;
      list.insertBefore(li, list.firstChild);
    }
    async function recordSession(m, secs) {
      secs = Math.round(secs);
      if (!secs || secs < 1) return;
      const params = { mode: m, seconds: secs };
      const sel = document.getElementById("focus-lesson");
      if (sel && sel.value) params.lesson_id = sel.value;
      const res = await postPartial("/focus/session", params);
      if (res && res.ok) { applyFocus(res.overview); prependRecord(res.record); }
    }

    function stop() {
      running = false; fstart.textContent = "Start";
      if (timer) { clearInterval(timer); timer = null; }
    }
    function start() {
      if (running) return;
      running = true; fstart.textContent = "Pause";
      timer = setInterval(() => {
        if (mode === "pomo") {
          remaining -= 1;
          if (remaining <= 0) {
            remaining = POMO; render(); stop();
            toast("Pomodoro complete 🍅");
            recordSession("pomo", POMO);
            return;
          }
        } else {
          elapsed += 1;
        }
        render();
      }, 1000);
    }

    fstart.addEventListener("click", () => (running ? stop() : start()));
    if (fend) fend.addEventListener("click", () => {
      if (mode !== "stopwatch" || elapsed <= 0) return;
      const secs = elapsed;
      stop(); elapsed = 0; render();
      recordSession("stopwatch", secs);
    });
    document.querySelectorAll("#focus-seg button").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#focus-seg button").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        mode = b.dataset.mode;
        stop();
        remaining = POMO; elapsed = 0;
        render();
      });
    });
    render();
  })();
})();
