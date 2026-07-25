// dnd.js — drag & drop for the Calendar (move a one-off event to another day).
// Framework-free progressive enhancement: the same edit is reachable via the
// event modal (date), so nothing here is load-bearing. Errors fall back to a
// full reload.
(() => {
  "use strict";
  const toast = (m) => { if (window.alUI && window.alUI.toast) window.alUI.toast(m); };

  async function post(url, params) {
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

  // ===== Calendar: drag a one-off event chip to another day cell =====
  (() => {
    const month = document.querySelector(".cal-month");
    if (!month) return;
    let dragged = null;

    month.addEventListener("dragstart", (e) => {
      const chip = e.target.closest('.cm-event.ev[draggable="true"]');
      if (!chip) return;
      dragged = chip;
      chip.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", chip.dataset.evId); } catch (_) {}
    });
    month.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("dragging");
      month.querySelectorAll(".cm-cell.over").forEach((c) => c.classList.remove("over"));
      dragged = null;
    });
    month.querySelectorAll(".cm-cell[data-date]").forEach((cell) => {
      cell.addEventListener("dragover", (e) => {
        if (!dragged || cell.dataset.date === dragged.dataset.evDate) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        cell.classList.add("over");
      });
      cell.addEventListener("dragleave", (e) => {
        if (!cell.contains(e.relatedTarget)) cell.classList.remove("over");
      });
      cell.addEventListener("drop", async (e) => {
        if (!dragged || cell.dataset.date === dragged.dataset.evDate) return;
        e.preventDefault();
        cell.classList.remove("over");
        const chip = dragged, newDate = cell.dataset.date;
        cell.appendChild(chip);                        // optimistic move
        const res = await post("/calendar/events/" + chip.dataset.evId + "/move",
          { date: newDate, return_to: "/calendar" });
        if (!res.ok) { toast(res.error || "could not move"); location.reload(); return; }
        chip.dataset.evDate = newDate;
        chip.href = chip.href.replace(/([?&]on=)[^&]*/, "$1" + newDate);
      });
    });
  })();
})();
