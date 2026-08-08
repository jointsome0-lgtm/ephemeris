// The focus timer drawer (#75) — one timer, reachable from every surface.
//
// The server owns the clock. Every response carries the whole state (`run`,
// `overview`, `recent`), and this file only interpolates between syncs: it
// renders `elapsed + (now - syncedAt)`, so a reload, a sleeping laptop or a
// throttled background tab cost accuracy, never the session (#20). Nothing
// here ever sends a duration.
//
// The drawer is markup in _timer_drawer.html, included by base.html, so a
// running timer keeps ticking across ordinary MPA navigation: each page load
// asks /focus/timer what is going on and picks the clock back up.

(() => {
  "use strict";

  const drawer = document.getElementById("timer-drawer");
  if (!drawer) return;

  const OPEN_KEY = "al-timer-open", MIN_KEY = "al-timer-min";
  const $ = (id) => document.getElementById(id);
  const els = {
    time: $("timer-time"), headClock: $("timer-head-clock"), on: $("timer-on"),
    ring: $("timer-ring"), today: $("timer-today"), setup: $("timer-setup"),
    mode: $("timer-mode"), lengths: $("timer-lengths"), custom: $("timer-custom"),
    target: $("timer-target"), note: $("timer-note"), error: $("timer-error"),
    recent: $("timer-recent"), start: $("timer-start"), pause: $("timer-pause"),
    stop: $("timer-stop"), discard: $("timer-discard"),
  };

  let run = null;          // the server's last word on the running timer
  let syncedAt = 0;        // Date.now() when that word arrived
  let ticker = null;
  let targetsLoading = null;  // the in-flight fetch, so two opens share one
  let mode = "countdown";
  let minutes = 25;
  let busy = false;
  let pendingTarget = null;  // a target picked from a row before the list loaded

  const toast = (msg) => (window.alUI && window.alUI.toast
    ? window.alUI.toast(msg) : undefined);

  // --- talking to the server -------------------------------------------------

  async function call(path, params) {
    busy = true;
    try {
      const r = await fetch(path, {
        method: "POST",
        headers: { "X-Partial": "1",
                   "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(params).toString(),
      });
      const data = await r.json();
      if (!data.ok) { showError(data.error || "could not save"); return null; }
      absorb(data);
      return data;
    } catch (_) {
      // A pre-#75 process still serving these pages has no /focus/timer route;
      // say so once instead of failing silently.
      showError("timer unavailable — is the app up to date?");
      return null;
    } finally {
      busy = false;
    }
  }

  async function sync() {
    try {
      const r = await fetch("/focus/timer", { headers: { "X-Partial": "1" } });
      if (!r.ok) return;
      absorb(await r.json());
    } catch (_) { /* offline: keep interpolating from the last sync */ }
  }

  function absorb(data) {
    run = data.run || null;
    syncedAt = Date.now();
    if (data.overview) {
      const ov = data.overview;
      els.today.textContent = ov.today_seconds
        ? ov.today_focus.value + ov.today_focus.unit + " today" : "";
    }
    if (data.recent) renderRecent(data.recent);
    showError("");
    render();
  }

  // --- rendering -------------------------------------------------------------

  // The server clamps a run at a day (focus.MAX_SECONDS): a laptop closed on
  // Friday must not record a weekend. Showing more than it will ever record
  // would just be a promise the Stop button then breaks.
  const MAX_SECONDS = 24 * 60 * 60;

  function liveElapsed() {
    if (!run) return 0;
    const drift = run.paused ? 0 : Math.max(0, (Date.now() - syncedAt) / 1000);
    return Math.min(MAX_SECONDS, Math.floor(run.elapsed + drift));
  }

  const clock = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    const two = (n) => String(n).padStart(2, "0");
    return h ? h + ":" + two(m) + ":" + two(s % 60) : two(m) + ":" + two(s % 60);
  };

  function render() {
    const running = !!run;
    drawer.dataset.state = !running ? "idle" : (run.paused ? "paused" : "running");
    els.setup.hidden = running;
    els.start.hidden = running;
    els.pause.hidden = !running;
    els.stop.hidden = !running;
    els.discard.hidden = !running;

    let shown, progress = 0;
    if (running) {
      const elapsed = liveElapsed();
      if (run.target_seconds) {
        shown = Math.max(0, run.target_seconds - elapsed);
        progress = Math.min(1, elapsed / run.target_seconds);
        if (shown <= 0 && !run.paused) autoFinish();
      } else {
        shown = elapsed;
        progress = (elapsed % 3600) / 3600;
      }
      els.pause.textContent = run.paused ? "Resume" : "Pause";
      els.on.textContent = run.target ? run.target.title : (run.note || "");
    } else {
      shown = mode === "countdown" ? minutes * 60 : 0;
      els.on.textContent = "";
    }
    els.time.textContent = clock(shown);
    els.headClock.textContent = clock(shown);
    els.headClock.hidden = !running;
    els.ring.style.setProperty("--focus-progress", String(progress));

    document.querySelectorAll(".timer-toggle").forEach((b) => {
      b.classList.toggle("timer-running", running && !run.paused);
      b.classList.toggle("timer-paused", running && run.paused);
    });
    tick(running && !run.paused);
  }

  function tick(on) {
    if (on && !ticker) ticker = setInterval(render, 1000);
    else if (!on && ticker) { clearInterval(ticker); ticker = null; }
  }

  function showError(msg) {
    els.error.textContent = msg || "";
    els.error.hidden = !msg;
  }

  function renderRecent(rows) {
    els.recent.textContent = "";
    rows.forEach((r) => {
      const li = document.createElement("li");
      li.className = "timer-rec";
      const dur = document.createElement("span");
      dur.className = "tr-dur";
      dur.textContent = r.duration_label;
      const sub = document.createElement("span");
      sub.className = "tr-sub";
      sub.textContent = r.target ? r.target.title : r.mode_label;
      const at = document.createElement("span");
      at.className = "tr-at";
      at.textContent = r.time_label;
      li.append(dur, sub, at);
      els.recent.appendChild(li);
    });
  }

  // --- the drawer itself -----------------------------------------------------

  function setOpen(open, minimized) {
    drawer.hidden = !open;
    drawer.setAttribute("aria-hidden", String(!open));
    if (minimized !== undefined) drawer.classList.toggle("minimized", minimized);
    try {
      localStorage.setItem(OPEN_KEY, open ? "1" : "0");
      if (minimized !== undefined) localStorage.setItem(MIN_KEY, minimized ? "1" : "0");
    } catch (_) {}
    if (open) { loadTargets(); sync(); }
  }

  function toggleTimer() {
    const opening = drawer.hidden;
    // Opening a minimized drawer should show it, not stay collapsed.
    setOpen(opening, opening ? false : undefined);
    if (opening && !run) (els.custom || els.start).focus();
  }

  // Rebuilt on every open, not cached for the session: tasks are completed and
  // habits archived without a page load, and offering one the server will
  // silently refuse loses the attribution the picker just promised.
  async function loadTargets() {
    if (targetsLoading) return targetsLoading;
    targetsLoading = (async () => {
      try {
        const r = await fetch("/focus/timer/targets", { headers: { "X-Partial": "1" } });
        const data = await r.json();
        if (!data.ok) return;
        const keep = pendingTarget || els.target.value;
        els.target.querySelectorAll("optgroup").forEach((g) => g.remove());
        const LABEL = { lesson: "Lessons", habit: "Habits", task: "Tasks" };
        Object.keys(LABEL).forEach((kind) => {
          const rows = data.targets[kind] || [];
          if (!rows.length) return;
          const group = document.createElement("optgroup");
          group.label = LABEL[kind];
          rows.forEach((row) => {
            const opt = document.createElement("option");
            opt.value = kind + ":" + row.id;
            opt.textContent = row.title;
            group.appendChild(opt);
          });
          els.target.appendChild(group);
        });
        // A selection whose row is gone simply does not take, which is the
        // honest outcome: the picker now shows what can still be focused on.
        if (keep) els.target.value = keep;
        pendingTarget = null;
      } catch (_) { /* keep whatever options are already on screen */ }
      finally { targetsLoading = null; }
    })();
    return targetsLoading;
  }

  // --- actions ---------------------------------------------------------------

  const newToken = () => (window.crypto && window.crypto.randomUUID
    ? window.crypto.randomUUID()
    : String(Date.now()) + "-" + Math.random().toString(16).slice(2));

  async function startTimer() {
    if (busy || run) return;
    const params = { token: newToken(), mode: mode };
    if (mode === "countdown") params.target_seconds = String(minutes * 60);
    const picked = els.target.value;
    if (picked) {
      const [kind, id] = picked.split(":");
      params[kind + "_id"] = id;
    }
    if (els.note.value.trim()) params.note = els.note.value.trim();
    const data = await call("/focus/timer/start", params);
    if (data) els.note.value = "";
  }

  async function autoFinish() {
    if (busy || !run) return;
    const done = await call("/focus/timer/finish", { token: run.token });
    if (done && done.recorded) toast("Focus recorded · " + done.recorded.duration_label);
  }

  async function stopTimer() {
    if (!run) return;
    const done = await call("/focus/timer/finish", { token: run.token });
    if (done && done.recorded) toast("Focus recorded · " + done.recorded.duration_label);
  }

  async function togglePause() {
    if (!run) return;
    await call("/focus/timer/pause", { token: run.token, paused: run.paused ? 0 : 1 });
  }

  async function discardTimer() {
    if (!run) return;
    await call("/focus/timer/discard", { token: run.token });
  }

  // --- wiring ----------------------------------------------------------------

  els.mode.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-mode]");
    if (!b) return;
    mode = b.dataset.mode;
    els.mode.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    els.lengths.hidden = mode === "open";
    render();
  });
  els.lengths.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-minutes]");
    if (!b) return;
    minutes = parseInt(b.dataset.minutes, 10);
    els.custom.value = "";
    els.lengths.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    render();
  });
  els.custom.addEventListener("input", () => {
    const v = parseInt(els.custom.value, 10);
    if (v > 0) {
      minutes = Math.min(v, 480);
      els.lengths.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      render();
    }
  });
  els.start.addEventListener("click", startTimer);
  els.pause.addEventListener("click", togglePause);
  els.stop.addEventListener("click", stopTimer);
  els.discard.addEventListener("click", discardTimer);
  $("timer-close").addEventListener("click", () => setOpen(false));
  $("timer-min").addEventListener("click", () =>
    setOpen(true, !drawer.classList.contains("minimized")));

  document.querySelectorAll(".timer-toggle").forEach((b) =>
    b.addEventListener("click", toggleTimer));

  // Any surface can hand the drawer a target: a habit row's timer button, a
  // lesson's "focus on this". Prefilling beats making the user find the row
  // again inside a picker they just came from.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-timer-target]");
    if (!btn) return;
    e.preventDefault();
    // Applied now if the option is already there, and again once the refreshed
    // list lands — the row can be clicked before the picker has ever loaded.
    pendingTarget = btn.getAttribute("data-timer-target");
    els.target.value = pendingTarget;
    if (drawer.hidden) setOpen(true, false); else loadTargets();
  });

  // Coming back to a tab that slept: the interpolated clock has drifted, and a
  // countdown may well have finished while nothing was rendering.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) sync();
  });

  window.alUI = Object.assign(window.alUI || {}, { toggleTimer });

  try {
    if (localStorage.getItem(OPEN_KEY) === "1") {
      drawer.hidden = false;
      drawer.setAttribute("aria-hidden", "false");
      drawer.classList.toggle("minimized", localStorage.getItem(MIN_KEY) === "1");
      loadTargets();
    }
  } catch (_) {}
  render();
  sync();  // the rail pip must show a running timer even with the drawer closed
})();
