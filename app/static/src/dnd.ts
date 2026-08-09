/* GENERATED-SOURCE NOTICE: app/static/dnd.js is emitted from this file by
 * `bun run build` (tsc, issue #42) and committed so deploy stays zero-build.
 * Edit THIS file and re-emit; never edit the .js by hand. */

/* dnd.ts — drag & drop for the Calendar (move a one-off event to another day).
 * Framework-free progressive enhancement: the same edit is reachable via the
 * event modal (date), so nothing here is load-bearing. Errors fall back to a
 * full reload.
 *
 * Loaded as a classic deferred script, not a module, so this file stays a
 * global script: no top-level import/export, and `window.alUI` is reached by a
 * local cast rather than a global augmentation (the same shape learn-bridge.ts
 * uses — one file's idea of the global namespace should not become every
 * file's). */
(() => {
  "use strict";

  interface MoveAnswer { ok?: boolean; error?: string }

  const toast = (m: string): void => {
    const ui = (window as unknown as {
      alUI?: { toast?: (msg: string) => void };
    }).alUI;
    if (ui && ui.toast) ui.toast(m);
  };

  async function post(url: string, params: Record<string, string>): Promise<MoveAnswer> {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "X-Partial": "1", "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams(params).toString(),
      });
      return await r.json() as MoveAnswer;
    } catch (_) {
      return { ok: false, error: "network error" };
    }
  }

  // ===== Calendar: drag a one-off event chip to another day cell =====
  (() => {
    const month = document.querySelector(".cal-month");
    if (!month) return;
    // The chip is the <a> the day cell holds, which is why the drop can rewrite
    // its href without asking the server for new markup.
    let dragged: HTMLAnchorElement | null = null;

    month.addEventListener("dragstart", (e) => {
      const target = (e as DragEvent).target as Element | null;
      const chip = target && target.closest<HTMLAnchorElement>('.cm-event.ev[draggable="true"]');
      if (!chip) return;
      dragged = chip;
      chip.classList.add("dragging");
      const dt = (e as DragEvent).dataTransfer;
      if (!dt) return;
      dt.effectAllowed = "move";
      try { dt.setData("text/plain", chip.dataset["evId"] ?? ""); } catch (_) { /* Safari */ }
    });
    month.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("dragging");
      month.querySelectorAll(".cm-cell.over").forEach((c) => c.classList.remove("over"));
      dragged = null;
    });
    month.querySelectorAll<HTMLElement>(".cm-cell[data-date]").forEach((cell) => {
      cell.addEventListener("dragover", (e) => {
        if (!dragged || cell.dataset["date"] === dragged.dataset["evDate"]) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
        cell.classList.add("over");
      });
      cell.addEventListener("dragleave", (e) => {
        if (!cell.contains(e.relatedTarget as Node | null)) cell.classList.remove("over");
      });
      cell.addEventListener("drop", async (e) => {
        if (!dragged || cell.dataset["date"] === dragged.dataset["evDate"]) return;
        e.preventDefault();
        cell.classList.remove("over");
        const chip = dragged, newDate = cell.dataset["date"] ?? "";
        cell.appendChild(chip);                        // optimistic move
        const res = await post("/calendar/events/" + (chip.dataset["evId"] ?? "") + "/move",
          { date: newDate, return_to: "/calendar" });
        if (!res.ok) { toast(res.error || "could not move"); location.reload(); return; }
        chip.dataset["evDate"] = newDate;
        chip.href = chip.href.replace(/([?&]on=)[^&]*/, "$1" + newDate);
      });
    });
  })();
})();
