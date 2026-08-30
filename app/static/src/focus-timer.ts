/* GENERATED-SOURCE NOTICE: app/static/focus-timer.js is emitted from this file
 * by `bun run build` (tsc, issue #42) and committed so deploy stays zero-build.
 * Edit THIS file and re-emit; never edit the .js by hand. */

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
//
// Loaded as a classic deferred script, not a module: no top-level
// import/export. `window.alUI` is declared by app.ts — this file adds
// `toggleTimer` to it.

(() => {
  "use strict";

  /** What the run is attributed to, when it is attributed to anything. */
  interface TimerTargetRef { kind?: string; id?: number | string; title: string }
  /** focus._run_view — the whole state the drawer renders itself from. */
  interface TimerRun {
    token: string;
    paused: boolean;
    elapsed: number;
    target_seconds?: number | null;
    note?: string | null;
    target?: TimerTargetRef | null;
  }
  /** focus.overview — today's total, already split into value + unit. */
  interface TimerOverview {
    today_seconds: number;
    today_focus: { value: number; unit: string };
  }
  /** focus._record_view — one finished span in the drawer's recent list. */
  interface TimerRecord {
    duration_label: string;
    mode_label: string;
    time_label: string;
    note?: string | null;
    target?: TimerTargetRef | null;
  }
  /** Every /focus/timer answer carries the whole state; the write routes add
   *  `recorded` when a span was stored. A refusal carries `error` instead. */
  interface TimerState {
    ok: boolean;
    error?: string;
    run: TimerRun | null;
    overview: TimerOverview;
    recent: TimerRecord[];
    recorded?: TimerRecord | null;
  }
  /** GET /focus/timer/targets — what the picker can offer, by kind. */
  type TargetsAnswer =
    | { ok: false }
    | { ok: true; targets: Record<string, Array<{ id: number | string; title: string }>> };

  const drawer = document.getElementById("timer-drawer");
  if (!drawer) return;

  const OPEN_KEY = "al-timer-open", MIN_KEY = "al-timer-min";
  // Every id below belongs to _timer_drawer.html, which the guard above has
  // just confirmed is in the page — so the lookup cannot miss, and the type
  // argument says which element the one template renders there.
  const $ = <T extends HTMLElement = HTMLElement>(id: string): T =>
    document.getElementById(id) as T;
  const els = {
    time: $("timer-time"), headClock: $("timer-head-clock"), on: $("timer-on"),
    ring: $("timer-ring"), today: $("timer-today"), setup: $("timer-setup"),
    mode: $("timer-mode"), lengths: $("timer-lengths"),
    custom: $<HTMLInputElement>("timer-custom"),
    target: $<HTMLSelectElement>("timer-target"),
    note: $<HTMLInputElement>("timer-note"), error: $("timer-error"),
    recent: $("timer-recent"), start: $("timer-start"), pause: $("timer-pause"),
    stop: $("timer-stop"), discard: $("timer-discard"),
  };

  let run: TimerRun | null = null;   // the server's last word on the running timer
  let syncedAt = 0;        // Date.now() when that word arrived
  let ticker: number | undefined;
  let poller: number | undefined;    // the resync loop; its period follows `run`
  let pollEvery = 0;       // the period that loop is currently running at
  let targetsLoading: Promise<void> | null = null;  // the in-flight fetch, so two opens share one
  let mode = "countdown";
  let minutes = 25;
  let preset = 25;   // the last length chosen from the buttons
  let busy = false;
  let issueSeq = 0;        // every request takes a ticket when it is sent
  let appliedSeq = 0;      // the ticket of the state currently on screen
  let pendingTarget: string | null = null;  // a target picked from a row before the list loaded

  const RETRY_MS = 3000;         // first look back after a lost answer…
  const RETRY_CEILING_MS = 12000;  // …then 6s and 12s, well past the 5s lock wait
  const POLL_MS = 30000;   // resync period while a timer is running
  const IDLE_POLL_MS = 120000;  // …and while there is none: only a start to miss

  const toast = (msg: string) => (window.alUI && window.alUI.toast
    ? window.alUI.toast(msg) : undefined);

  // --- talking to the server -------------------------------------------------

  async function call(path: string, params: Record<string, string>): Promise<TimerState | null> {
    busy = true;
    const ticket = ++issueSeq;
    let refused = false;
    try {
      const r = await fetch(path, {
        method: "POST",
        headers: { "X-Partial": "1",
                   "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(params).toString(),
      });
      const data = await r.json() as TimerState;
      if (!data.ok) {
        showError(data.error || "could not save");
        refused = true;
        return null;
      }
      absorb(data, ticket);
      if (channel) channel.postMessage(1);
      return data;
    } catch (_) {
      showError("timer unavailable");
      // An answer that never arrives says nothing about the server: the write
      // may well have committed just before the connection dropped. Ask what is
      // actually running, rather than assuming nothing happened and sitting
      // idle over a timer that is.
      refused = true;
      resyncAfterLostAnswer();
      return null;
    } finally {
      busy = false;
      // A refusal usually means this tab is out of date — another tab discarded
      // the run, or it was already recorded. Without asking, the drawer would
      // keep showing a timer nobody has, and a finished countdown would retry
      // autoFinish every second forever.
      if (refused) sync(true);
    }
  }

  async function sync(keepError?: boolean): Promise<boolean> {
    if (busy) return false;  // a write is in flight; its answer is the newer one
    const ticket = ++issueSeq;
    try {
      const r = await fetch("/focus/timer", { headers: { "X-Partial": "1" } });
      if (!r.ok) return false;
      absorb(await r.json() as TimerState, ticket, keepError);
      return true;
    } catch (_) { /* offline: keep interpolating from the last sync */ }
    return false;
  }

  // Keep asking after a write whose answer was lost. It may still have been
  // queued behind the writer lock, which waits up to five seconds (app/db.py),
  // so an early read can see a database that has not changed yet. The last of
  // these lands well past that window; a drawer that stops asking sooner sits
  // over a timer the server is still running. Once a run is visible, `poll()`
  // below keeps it honest.
  async function resyncAfterLostAnswer(): Promise<void> {
    for (let wait = RETRY_MS; wait <= RETRY_CEILING_MS; wait *= 2) {
      await new Promise((settle) => setTimeout(settle, wait));
      await sync(true);
    }
  }

  // Tickets are handed out in send order. A read issued before a write can
  // still answer after it — describing a world without the run the user just
  // started — so an older answer loses instead of erasing the newer one.
  function absorb(data: TimerState, ticket: number, keepError?: boolean): void {
    if (ticket <= appliedSeq) return;
    appliedSeq = ticket;
    run = data.run;
    syncedAt = Date.now();
    const ov = data.overview;
    els.today.textContent = ov.today_seconds
      ? ov.today_focus.value + ov.today_focus.unit + " today" : "";
    renderRecent(data.recent);
    // ...except the one that sent us here: a resync after a refusal is what
    // explains the refusal, so its message stays on screen.
    if (!keepError) showError("");
    render();
  }

  // --- rendering -------------------------------------------------------------

  // The server clamps a run at a day (focus.MAX_SECONDS): a laptop closed on
  // Friday must not record a weekend. Showing more than it will ever record
  // would just be a promise the Stop button then breaks.
  const MAX_SECONDS = 24 * 60 * 60;

  function liveElapsed(): number {
    if (!run) return 0;
    const drift = run.paused ? 0 : Math.max(0, (Date.now() - syncedAt) / 1000);
    return Math.min(MAX_SECONDS, Math.floor(run.elapsed + drift));
  }

  const clock = (s: number): string => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    const two = (n: number) => String(n).padStart(2, "0");
    return h ? h + ":" + two(m) + ":" + two(s % 60) : two(m) + ":" + two(s % 60);
  };

  function render(): void {
    // One read of the mutable run per frame, into a const: the handler below
    // is a closure, and a `let` that other code reassigns carries no narrowing
    // into one. Single-threaded, so this is the same value throughout either way.
    const active = run;
    const running = !!active;
    drawer!.dataset["state"] = !active ? "idle" : (active.paused ? "paused" : "running");
    els.setup.hidden = running;
    els.start.hidden = running;
    els.pause.hidden = !running;
    els.stop.hidden = !running;
    els.discard.hidden = !running;

    let shown, progress = 0;
    if (active) {
      const elapsed = liveElapsed();
      if (active.target_seconds) {
        shown = Math.max(0, active.target_seconds - elapsed);
        progress = Math.min(1, elapsed / active.target_seconds);
        if (shown <= 0 && !active.paused) autoFinish();
      } else {
        shown = elapsed;
        progress = (elapsed % 3600) / 3600;
      }
      els.pause.textContent = active.paused ? "Resume" : "Pause";
      els.on.textContent = active.target ? active.target.title : (active.note || "");
    } else {
      shown = mode === "countdown" ? minutes * 60 : 0;
      els.on.textContent = "";
    }
    els.time.textContent = clock(shown);
    els.headClock.textContent = clock(shown);
    els.headClock.hidden = !running;
    els.ring.style.setProperty("--focus-progress", String(progress));

    document.querySelectorAll(".timer-toggle").forEach((b) => {
      b.classList.toggle("timer-running", !!active && !active.paused);
      b.classList.toggle("timer-paused", !!active && active.paused);
    });
    tick(!!active && !active.paused);
    poll();
  }

  function tick(on: boolean): void {
    if (on && !ticker) ticker = setInterval(render, 1000);
    else if (!on && ticker) { clearInterval(ticker); ticker = undefined; }
  }

  // Two windows can be visible at once, and no `visibilitychange` fires in the
  // one that was not touched: without asking, it would keep counting a timer
  // the other stopped — or miss one the other started — for as long as it
  // stayed open. So every tab keeps asking, quickly while it has a timer to be
  // wrong about and slowly while it has none. `channel` below usually gets
  // there first; this is what covers a second browser, or a phone.
  function poll(): void {
    const wanted = run ? POLL_MS : IDLE_POLL_MS;
    if (poller && pollEvery === wanted) return;
    if (poller) clearInterval(poller);
    pollEvery = wanted;
    poller = setInterval(() => sync(), wanted);
  }

  // Same browser, same profile: a write in one tab tells the others at once,
  // so the common case costs no polling latency. Nothing is sent but a nudge —
  // the state itself always comes from the server, which is the only clock.
  const channel = ("BroadcastChannel" in window)
    ? new BroadcastChannel("al-focus-timer") : null;
  if (channel) channel.onmessage = () => sync();

  function showError(msg: string): void {
    els.error.textContent = msg || "";
    els.error.hidden = !msg;
  }

  function renderRecent(rows: TimerRecord[]): void {
    els.recent.textContent = "";
    rows.forEach((r) => {
      const li = document.createElement("li");
      li.className = "timer-rec";
      const dur = document.createElement("span");
      dur.className = "tr-dur";
      dur.textContent = r.duration_label;
      const sub = document.createElement("span");
      sub.className = "tr-sub";
      // What it was, most specific first: the thing focused on, then whatever
      // the user typed about it. Without this the note is write-only.
      const said = [r.target ? r.target.title : null, r.note].filter(Boolean);
      sub.textContent = said.join(" · ") || r.mode_label;
      sub.title = sub.textContent;
      const at = document.createElement("span");
      at.className = "tr-at";
      at.textContent = r.time_label;
      li.append(dur, sub, at);
      els.recent.appendChild(li);
    });
  }

  // --- the drawer itself -----------------------------------------------------

  function setOpen(open: boolean, minimized?: boolean): void {
    drawer!.hidden = !open;
    drawer!.setAttribute("aria-hidden", String(!open));
    if (minimized !== undefined) drawer!.classList.toggle("minimized", minimized);
    try {
      localStorage.setItem(OPEN_KEY, open ? "1" : "0");
      if (minimized !== undefined) localStorage.setItem(MIN_KEY, minimized ? "1" : "0");
    } catch (_) {}
    if (open) { loadTargets(); sync(); }
  }

  function toggleTimer(): void {
    const opening = drawer!.hidden;
    // Opening a minimized drawer should show it, not stay collapsed.
    setOpen(opening, opening ? false : undefined);
    if (opening && !run) (els.custom || els.start).focus();
  }

  // Rebuilt on every open, not cached for the session: tasks are completed and
  // habits archived without a page load, and offering one the server will
  // silently refuse loses the attribution the picker just promised.
  async function loadTargets(): Promise<void> {
    if (targetsLoading) return targetsLoading;
    targetsLoading = (async () => {
      try {
        const r = await fetch("/focus/timer/targets", { headers: { "X-Partial": "1" } });
        const data = await r.json() as TargetsAnswer;
        if (!data.ok) return;
        const keep = pendingTarget || els.target.value;
        els.target.querySelectorAll("optgroup").forEach((g) => g.remove());
        const LABEL = { lesson: "Lessons", habit: "Habits", task: "Tasks" };
        (Object.keys(LABEL) as Array<keyof typeof LABEL>).forEach((kind) => {
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

  async function startTimer(): Promise<void> {
    if (busy || run) return;
    // A row's timer button can open the drawer and be followed by Start before
    // the picker's options arrive; until then the choice lives only in
    // pendingTarget, and starting without it drops the attribution the button
    // just promised.
    if (targetsLoading) await targetsLoading;
    if (busy || run) return;  // a second click got here first while we waited
    const params: Record<string, string> = { token: newToken(), mode: mode };
    // The length goes out as typed, capped by nobody here: the server owns the
    // range, and quietly starting a different timer than the field shows would
    // be worse than being told the number is out of bounds.
    if (mode === "countdown") params["target_seconds"] = String(Math.round(minutes * 60));
    const picked = els.target.value || pendingTarget || "";
    if (picked) {
      const [kind, id] = picked.split(":");
      params[kind + "_id"] = id ?? "";
    }
    if (els.note.value.trim()) params["note"] = els.note.value.trim();
    const data = await call("/focus/timer/start", params);
    if (data) els.note.value = "";
    // The drawer can stay open for hours while the board behind it moves, so
    // the target it offers may have been completed or archived since. The
    // server refuses rather than silently dropping the attribution; reloading
    // makes the picker tell the same story as the refusal.
    else if (picked) loadTargets();
  }

  async function autoFinish(): Promise<void> {
    if (busy || !run) return;
    const done = await call("/focus/timer/finish", { token: run.token });
    if (done && done.recorded) toast("Focus recorded · " + done.recorded.duration_label);
  }

  // Every write waits its turn, the way Start does. Two clicks that overlap are
  // not two decisions: the second reads a screen the first has not answered
  // yet, so a doubled Pause could be followed by a Resume that overtakes it and
  // leaves the drawer showing "paused" while the server keeps counting.
  async function stopTimer(): Promise<void> {
    if (busy || !run) return;
    const done = await call("/focus/timer/finish", { token: run.token });
    if (done && done.recorded) toast("Focus recorded · " + done.recorded.duration_label);
  }

  async function togglePause(): Promise<void> {
    if (busy || !run) return;
    await call("/focus/timer/pause", { token: run.token, paused: run.paused ? "0" : "1" });
  }

  async function discardTimer(): Promise<void> {
    if (busy || !run) return;
    await call("/focus/timer/discard", { token: run.token });
  }

  // --- wiring ----------------------------------------------------------------

  els.mode.addEventListener("click", (e) => {
    const b = (e.target as Element).closest<HTMLElement>("button[data-mode]");
    if (!b) return;
    mode = b.dataset["mode"] ?? mode;   // the selector guarantees the attribute
    els.mode.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    els.lengths.hidden = mode === "open";
    render();
  });
  els.lengths.addEventListener("click", (e) => {
    const b = (e.target as Element).closest<HTMLElement>("button[data-minutes]");
    if (!b) return;
    preset = parseInt(b.dataset["minutes"] ?? "", 10);
    minutes = preset;
    els.custom.value = "";
    els.lengths.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    render();
  });
  // The field always speaks for itself: cleared, it hands the length back to the
  // chosen preset; nonsense in it means nonsense goes to the server, which says
  // so. What it must never do is leave a stale number armed behind a value the
  // user has since changed.
  els.custom.addEventListener("input", () => {
    const raw = els.custom.value.trim();
    if (!raw) {
      minutes = preset;
      els.lengths.querySelectorAll("button").forEach(
        (x) => x.classList.toggle("on", parseInt(x.dataset["minutes"] ?? "", 10) === preset));
    } else {
      const v = Number(raw);
      minutes = Number.isFinite(v) && v > 0 ? v : 0;
      els.lengths.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
    }
    render();
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
    const btn = (e.target as Element).closest("[data-timer-target]");
    if (!btn) return;
    e.preventDefault();
    // Applied now if the option is already there, and again once the refreshed
    // list lands — the row can be clicked before the picker has ever loaded.
    pendingTarget = btn.getAttribute("data-timer-target");
    els.target.value = pendingTarget ?? "";
    // Always expanded, never merely open: a minimized drawer hides the setup and
    // Start controls, so the row's button would look like it did nothing.
    setOpen(true, false);
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
