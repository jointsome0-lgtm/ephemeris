// dnd.js — drag & drop for the Matrix (reprioritise + reorder) and the Calendar
// (move a one-off event to another day). Framework-free progressive enhancement:
// the same edits are reachable via the task detail (priority) and the event modal
// (date), so nothing here is load-bearing. Errors fall back to a full reload.
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

  // ===== Matrix: drag a task within a quadrant (reorder) or across (reprioritise) =====
  (() => {
    const grid = document.querySelector(".matrix-grid");
    if (!grid) return;
    let dragged = null;

    // the .trow whose top half the cursor is over → insert before it (null = append)
    function refUnder(body, y) {
      return [...body.querySelectorAll(".trow:not(.dragging)")].find((r) => {
        const b = r.getBoundingClientRect();
        return y < b.top + b.height / 2;
      }) || null;
    }

    // keep each quadrant's count badge honest after a move (the empty placeholder
    // is toggled by CSS :has(.trow), so nothing to manage here)
    function sync() {
      grid.querySelectorAll(".quad").forEach((quad) => {
        const n = quad.querySelectorAll(".trow").length;
        let badge = quad.querySelector(".quad-count");
        if (n) {
          if (!badge) {
            badge = document.createElement("span");
            badge.className = "quad-count";
            quad.querySelector(".quad-head").appendChild(badge);
          }
          badge.textContent = n;
        } else if (badge) {
          badge.remove();
        }
      });
    }

    async function moveTo(body, refRow) {
      const row = dragged;
      const priority = body.closest(".quad").dataset.priority;
      const cbx = row.querySelector(".checkbox");           // retint to the new quadrant
      if (cbx) cbx.className = cbx.className.replace(/\bp[0-3]\b/, "p" + priority);
      body.insertBefore(row, refRow || body.querySelector(".quad-empty"));
      const prev = row.previousElementSibling, next = row.nextElementSibling;
      const after = prev && prev.classList.contains("trow") ? prev.dataset.id : "";
      const before = next && next.classList.contains("trow") ? next.dataset.id : "";
      sync();
      const res = await post("/tasks/" + row.dataset.id + "/move",
        { priority, after, before, return_to: "/matrix" });
      if (!res.ok) { toast(res.error || "could not move"); location.reload(); }
    }

    grid.addEventListener("dragstart", (e) => {
      const row = e.target.closest(".trow");
      if (!row) return;
      dragged = row;
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", row.dataset.id); } catch (_) {}
    });
    grid.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("dragging");
      grid.querySelectorAll(".quad-body.over").forEach((b) => b.classList.remove("over"));
      dragged = null;
    });
    grid.querySelectorAll(".quad-body").forEach((body) => {
      body.addEventListener("dragover", (e) => {
        if (!dragged) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        body.classList.add("over");
      });
      body.addEventListener("dragleave", (e) => {
        if (!body.contains(e.relatedTarget)) body.classList.remove("over");
      });
      body.addEventListener("drop", (e) => {
        if (!dragged) return;
        e.preventDefault();
        body.classList.remove("over");
        moveTo(body, refUnder(body, e.clientY));
      });
    });

    // keyboard fallback on a focused row: [ = more urgent, ] = less urgent
    grid.addEventListener("keydown", (e) => {
      if (e.key !== "[" && e.key !== "]") return;
      const row = e.target.closest(".trow");
      if (!row) return;
      const pri = parseInt(row.closest(".quad").dataset.priority, 10);
      const target = e.key === "[" ? Math.min(3, pri + 1) : Math.max(0, pri - 1);
      if (target === pri) return;
      e.preventDefault();
      const destBody = grid.querySelector('.quad[data-priority="' + target + '"] .quad-body');
      if (!destBody) return;
      dragged = row;
      moveTo(destBody, null).then(() => { row.focus(); dragged = null; });
    });
  })();

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
