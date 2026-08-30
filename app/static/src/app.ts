/* GENERATED-SOURCE NOTICE: app/static/app.js is emitted from this file by
 * `bun run build` (tsc, issue #42) and committed so deploy stays zero-build.
 * Edit THIS file and re-emit; never edit the .js by hand. */

// TickTick-clone — Mode B progressive enhancement.
// Framework-free. The server-rendered forms (Mode A) work without this file; here
// we intercept them to fetch + patch the DOM in place (no full reload). The data
// contract is identical, so disabling JS just falls back to 303-redirect reloads.
//
// Loaded as a classic deferred script, NOT a module: no top-level import/export,
// or the browser would treat the emit as an ES module and never run it. That is
// also why the declarations below work — a global script contributes its types
// to the global scope, so every other src/*.ts sees this file's idea of
// `window.alUI` instead of keeping a private one.

/** The optional action button a toast can carry (e.g. Undo). */
interface AlToastAction { label: string; fn: () => void }

/** The shared UI surface. Assembled by more than one script — this file defines
 *  the five below, focus-timer.js adds `toggleTimer` wherever the drawer is in
 *  the page — so every member is optional and every caller tests the one it
 *  wants, which is exactly what the code did before it was typed. */
interface AlUI {
  newTask?: () => void;
  toggleTheme?: () => void;
  showHints?: () => void;
  closeHints?: () => void;
  toast?: (msg: string, action?: AlToastAction) => void;
  toggleTimer?: () => void;
}

type AlThemePref = "system" | "light" | "dark";
type AlScheme = "light" | "dark";

/** What base.html's head script hands over as `mq`: a real MediaQueryList
 *  wherever `matchMedia` exists and a static stand-in where it does not, which
 *  is why neither listener spelling can be assumed present. */
interface AlThemeMedia {
  matches: boolean;
  addEventListener?: ((type: "change", listener: () => void) => void) | null;
  addListener?: ((listener: () => void) => void) | null;
}

/** The one owner of the theme storage key, the order, the resolve rule and the
 *  system media query: base.html's pre-paint head script, which always runs
 *  before this deferred file. */
interface AlTheme {
  KEY: string;
  ORDER: readonly AlThemePref[];
  read: () => AlThemePref;
  save: (pref: AlThemePref) => void;
  resolve: (pref: AlThemePref) => AlScheme;
  apply: (pref: AlThemePref) => AlScheme;
  mq: AlThemeMedia;
}

interface Window {
  alUI?: AlUI;
  alTheme: AlTheme;
}

(() => {
  "use strict";

  /** A route that refused: the only two fields Mode B reads off a refusal. */
  interface Refused { ok: false; error?: string }
  /** POST /checkins — the habit row's new state. */
  interface CheckinOk {
    ok: true;
    item_id: number;
    status: string | null;
    current_streak: number;
    total: number;
  }
  /** POST /tasks/<id>/complete */
  interface CompleteOk { ok: true; completed: boolean }
  /** POST /daily-note */
  interface SavedOk { ok: true }

  /** POST form-encoded data and parse the JSON partial response. */
  async function postPartial<T>(url: string, params: Record<string, string>): Promise<T | Refused> {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "X-Partial": "1", "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(params).toString(),
      });
      return await r.json() as T;
    } catch (_) {
      return { ok: false, error: "network error" };
    }
  }

  let toastTimer: number | undefined;
  // toast(msg) — transient status. toast(msg, {label, fn}) — adds an action
  // button (e.g. Undo) and holds longer so it can be clicked.
  function toast(msg: string, action?: AlToastAction): void {
    let found = document.querySelector(".toast");
    if (!found) {
      found = document.createElement("div");
      found.className = "toast";
      document.body.appendChild(found);
    }
    const el = found;   // a const, so the action handler below can close over it
    el.textContent = "";
    const span = document.createElement("span");
    span.textContent = msg;
    el.appendChild(span);
    const hasAction = !!(action && action.label && typeof action.fn === "function");
    if (action && hasAction) {
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
  function applyCheckin(state: CheckinOk): void {
    const row = document.getElementById("item-" + state.item_id);
    if (!row) return;
    const done = !!state.status;
    row.dataset["status"] = state.status || "";
    const ring = row.querySelector("[data-dot]");
    if (ring) ring.classList.toggle("done", done);
    if (typeof state.current_streak === "number") {
      const cur = row.querySelector("[data-streak-cur]");
      if (cur) cur.textContent = String(state.current_streak);
    }
    if (typeof state.total === "number") {
      const tot = row.querySelector("[data-total]");
      if (tot) tot.textContent = String(state.total);
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
      // Every field in these forms is a text control, so FormData yields only
      // strings here; a File value would need a different body encoding.
      const res = await postPartial<CheckinOk>(
        "/checkins", Object.fromEntries(new FormData(form)) as Record<string, string>);
      if (res.ok) applyCheckin(res);
      else toast(res.error || "could not save");
    } else if (/^\/tasks\/\d+\/complete$/.test(action)) {
      e.preventDefault();
      const row = form.closest(".trow");
      const res = await postPartial<CompleteOk>(
        action, Object.fromEntries(new FormData(form)) as Record<string, string>);
      if (!res.ok) { toast(res.error || "could not save"); return; }
      if (row) row.classList.toggle("done", res.completed);
      const cb = form.querySelector(".checkbox");
      if (cb) cb.classList.toggle("on", res.completed);
      if (res.completed) {
        toast("Task completed", { label: "Undo", fn: async () => {
          const undo = await postPartial<CompleteOk>(
            action, Object.fromEntries(new FormData(form)) as Record<string, string>);
          if (undo.ok) {
            if (row) row.classList.toggle("done", undo.completed);
            if (cb) cb.classList.toggle("on", undo.completed);
          }
        } });
      }
    } else if (action === "/daily-note") {
      e.preventDefault();
      const res = await postPartial<SavedOk>(
        "/daily-note", Object.fromEntries(new FormData(form)) as Record<string, string>);
      toast(res.ok ? "Daily note saved" : (res.error || "could not save"));
    }
  });

  // (The Learn preview poll moved to learn-bridge.ts (D2): the bridge grant
  // is bound to the loaded revision, so one runtime owns poll and handshake.)

  // --- Learn workspace: draggable list/preview split + collapsible list -------
  // The list width is the --lesson-w grid track on .learn-workspace; both the
  // width (al-learn-w) and the collapsed state (al-learn-min) persist. Desktop
  // only — below 860px the workspace stacks and the gutter is display:none.
  (() => {
    const ws = document.querySelector<HTMLElement>(".learn-workspace");
    const split = document.getElementById("learn-split");
    const btn = document.getElementById("learn-split-btn");
    if (!ws || !split || !btn) return;
    const panel = ws.querySelector(".lesson-panel");
    const W_KEY = "al-learn-w", MIN_KEY = "al-learn-min";
    const MIN_LIST = 250, MIN_PREVIEW = 320;

    // The guard above proves all three are there, but these two are hoisted
    // function declarations: to the compiler they exist before the guard has
    // run, so each use has to say so again.
    function applyWidth(w: number): void {
      const max = Math.max(ws!.clientWidth - MIN_PREVIEW - split!.offsetWidth - 4, MIN_LIST);
      ws!.style.setProperty("--lesson-w", Math.round(Math.min(Math.max(w, MIN_LIST), max)) + "px");
    }
    function applyCollapsed(min: boolean): void {
      ws!.classList.toggle("panel-collapsed", min);
      const label = min ? "Expand lesson list" : "Collapse lesson list";
      btn!.title = label;
      btn!.setAttribute("aria-label", label);
      btn!.setAttribute("aria-expanded", String(!min));
    }
    const currentWidth = () => parseInt(ws.style.getPropertyValue("--lesson-w"), 10);

    const savedW = parseInt(localStorage.getItem(W_KEY) ?? "", 10);
    if (savedW > 0) applyWidth(savedW);
    applyCollapsed(localStorage.getItem(MIN_KEY) === "1");

    btn.addEventListener("click", () => {
      const min = !ws.classList.contains("panel-collapsed");
      applyCollapsed(min);
      try { localStorage.setItem(MIN_KEY, min ? "1" : "0"); } catch (_) {}
    });

    split.addEventListener("mousedown", (e) => {
      if (btn.contains(e.target as Node | null) || ws.classList.contains("panel-collapsed")
          || !panel) return;
      e.preventDefault();
      const startX = e.clientX;
      const startW = panel.getBoundingClientRect().width;
      ws.classList.add("splitting");
      document.body.style.userSelect = "none";
      const onMove = (ev: MouseEvent) => applyWidth(startW + ev.clientX - startX);
      const end = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", end);
        ws.classList.remove("splitting");
        document.body.style.userSelect = "";
        const w = currentWidth();
        if (w) { try { localStorage.setItem(W_KEY, String(w)); } catch (_) {} }
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", end);
    });

    // keep a saved width inside bounds when the window shrinks
    window.addEventListener("resize", () => {
      const w = currentWidth();
      if (w) applyWidth(w);
    });
  })();

  // (The Learn record sheet's open state and height cap are inline in
  // learn.html: the deferred Learn runtime reads `open` to decide what the
  // learner has already seen, so restoring it has to happen during parsing.)

  // --- theme: tri-state (system | light | dark); default follows the OS --------
  // The storage key, resolve rule and system media query live in ONE place:
  // window.alTheme, defined by base.html's pre-paint head script (which always
  // runs before us) — this block only drives the toggle through it.
  (() => {
    const btns = document.querySelectorAll<HTMLElement>(".theme-toggle");
    const { KEY, ORDER, read, save, apply: applyScheme, mq } = window.alTheme;
    const LABEL = { system: "System", light: "Light", dark: "Dark" };
    function paint(pref: AlThemePref): void {
      applyScheme(pref);  // data-theme + the server-visible mirror, one owner
      btns.forEach((b) => {
        b.dataset["pref"] = pref;
        b.title = "Theme: " + LABEL[pref];
        b.setAttribute("aria-label", "Theme: " + LABEL[pref] + " — tap to change");
      });
    }
    function apply(pref: AlThemePref): void { paint(pref); save(pref); }
    // The modulo keeps the index inside ORDER, so the fallback below is never
    // taken; "system" is the same default alTheme.read() answers with.
    btns.forEach((b) => b.addEventListener("click", () =>
      apply(ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length] ?? "system")));
    // live-react to OS theme changes while in "system" mode
    const onSystemChange = () => { if (read() === "system") apply("system"); };
    if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
    else if (mq.addListener) mq.addListener(onSystemChange);
    // Another tab moved the preference. Adopt it: the resolved scheme is also
    // mirrored into an origin-wide cookie the server reads, so a tab left on
    // the old theme would have its next lesson-preview request answered in the
    // other tab's scheme. Paint without saving — the value is already stored,
    // and writing it back could bounce the event between tabs.
    window.addEventListener("storage", (e) => {
      if (e.key === KEY) paint(read());
    });
    apply(read());  // sync data-theme + button UI on load
  })();

  // --- global keyboard shortcuts + "?" cheat sheet ----------------------------
  // Chord nav (g→t/c/f/…), single-key actions, and a help overlay. The palette
  // (palette.js) owns ⌘K and reuses these action handlers via window.alUI.
  (() => {
    // `g t` goes wherever the rail's Tasks icon goes — the server decides that,
    // and reading the rendered link keeps the two from ever disagreeing.
    const tasksIcon = document.querySelector('.rail-ico[title="Tasks"]');
    const NAV: Record<string, string> = {
                  t: (tasksIcon && tasksIcon.getAttribute("href")) || "/today",
                  c: "/calendar",
                  h: "/habits", l: "/learn", s: "/search" };
    const HINTS: ReadonlyArray<readonly [string, string]> = [
      ["⌘K  Ctrl K", "Command palette"],
      ["n", "New task"],
      ["g t / c", "Tasks / Calendar"],
      ["g f", "Focus timer"],
      ["g h", "Habits"],
      ["g l / s", "Learn / Search"],
      ["t", "Toggle theme"],
      ["?", "This help"],
    ];

    // The nodeType test is what proves this is an Element, so the parameter is
    // typed as one; `isContentEditable` belongs to HTMLElement and simply reads
    // undefined on anything else, which is the duck-typing this relies on.
    function typing(el: Element | null): boolean {
      if (!el || el.nodeType !== 1) return false;  // only Elements can be edit targets
      return (el as HTMLElement).isContentEditable ||
        /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || !!el.closest(".term-drawer");
    }
    function newTask(): void {
      // Quick-add is an <input> on the surfaces that have one, but the selector
      // does not promise that, which is why `select` is tested before use.
      const qa = document.querySelector(".qa-input") as
        (HTMLElement & { select?: () => void }) | null;
      if (qa) { qa.focus(); if (qa.select) qa.select(); }
      else window.location.href = "/today";
    }
    function toggleTheme(): void {
      const b = document.querySelector<HTMLElement>(".theme-toggle");
      if (b) b.click();
    }
    let hintsEl: HTMLDivElement | null = null;
    function closeHints(): void { if (hintsEl) { hintsEl.remove(); hintsEl = null; } }
    function showHints(): void {
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

    let armed = false, armTimer: number | undefined;
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeHints(); return; }
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (typing(e.target as Element | null)) return;
      if (armed) {
        armed = false; clearTimeout(armTimer);
        const key = e.key.toLowerCase();
        // `g f` kept its finger memory when Focus stopped being a page (#75):
        // it now opens the timer drawer instead of navigating to it.
        if (key === "f" && window.alUI && window.alUI.toggleTimer) {
          e.preventDefault(); window.alUI.toggleTimer(); return;
        }
        const dest = NAV[key];
        if (dest) { e.preventDefault(); window.location.href = dest; }
        return;
      }
      if (e.key === "g") { armed = true; armTimer = setTimeout(() => (armed = false), 600); return; }
      if (e.key === "n") { e.preventDefault(); newTask(); }
      else if (e.key === "t") { e.preventDefault(); toggleTheme(); }
      else if (e.key === "?") { e.preventDefault(); showHints(); }
    });
  })();

})();
