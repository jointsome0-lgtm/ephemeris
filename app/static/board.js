// board.js — the kanban board's drag & drop (#53). Deliberately its own file
// next to dnd.js rather than a branch inside it: the calendar's drag moves an
// event to a day, this one moves a task to a column, and the only thing they
// share is the HTML5 DnD shape.
//
// Framework-free progressive enhancement. Every move here is also a plain POST
// form on the card (the ←/→ buttons), so with this file blocked or broken the
// board still works; errors fall back to a full reload, which re-renders the
// truth from the server.
(() => {
  "use strict";

  const board = document.getElementById("board");
  if (!board) return;

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

  const bodyOf = (status) => board.querySelector('.bcol-body[data-drop="' + status + '"]');

  // Column order and names come from what the server rendered, so this file
  // never carries a second copy of the board's shape.
  const ORDER = Array.from(board.querySelectorAll(".bcol")).map((c) => c.dataset.status);
  const LABEL = {};
  board.querySelectorAll(".bcol").forEach((c) => {
    const name = c.querySelector(".bcol-name");
    LABEL[c.dataset.status] = name ? name.textContent.trim() : c.dataset.status;
  });

  /** Recount every column header from what is actually in the DOM. */
  function recount() {
    board.querySelectorAll(".bcol").forEach((col) => {
      const n = col.querySelectorAll(".bcard").length;
      const badge = col.querySelector("[data-count]");
      if (badge) badge.textContent = n;
    });
  }

  /** Put a card in a column, before the empty-state line so the CSS rule that
   *  hides it (`.bcard ~ .bcol-empty`) keeps holding. */
  function place(card, status) {
    const body = bodyOf(status);
    if (!body) return false;
    const empty = body.querySelector(".bcol-empty");
    if (empty) body.insertBefore(card, empty);
    else body.appendChild(card);
    card.dataset.status = status;
    return true;
  }

  /** Rewrite a moved card's ←/→ targets and completed look for its new column.
   *  The server is still the author of both — this only keeps the card honest
   *  until the next full render. */
  function retarget(card, status, completed) {
    const i = ORDER.indexOf(status);
    const wanted = [ORDER[i - 1], ORDER[i + 1]];   // left form, then right form
    const title = card.querySelector(".bcard-title");
    const what = title ? title.textContent.trim() : "task";
    card.querySelectorAll(".bmove-form").forEach((form, n) => {
      const field = form.querySelector('input[name="status"]');
      const button = form.querySelector(".bmove");
      if (!wanted[n]) { form.hidden = true; return; }
      form.hidden = false;
      if (field) field.value = wanted[n];
      if (button) {
        button.title = "Move to " + LABEL[wanted[n]];
        button.setAttribute("aria-label", "Move “" + what + "” to " + LABEL[wanted[n]]);
      }
    });
    card.classList.toggle("done", !!completed);
  }

  async function move(card, status) {
    const from = card.dataset.status;
    if (!card || status === from) return;
    const home = bodyOf(from);
    if (!place(card, status)) return;          // optimistic
    recount();
    const res = await post("/tasks/" + card.dataset.taskId + "/status",
      { status: status, return_to: location.pathname });
    if (!res.ok) {
      if (home) place(card, from);             // put it back, then tell the truth
      recount();
      toast(res.error || "could not move");
      return;
    }
    retarget(card, res.status, res.completed);
  }

  // ===== the ←/→ buttons: same move, without the page reload =====
  board.addEventListener("submit", (e) => {
    const form = e.target.closest(".bmove-form");
    if (!form) return;
    e.preventDefault();
    const card = form.closest(".bcard");
    const field = form.querySelector('input[name="status"]');
    if (card && field) move(card, field.value);
  });

  // ===== drag a card to another column =====
  let dragged = null;

  board.addEventListener("dragstart", (e) => {
    const card = e.target.closest('.bcard[draggable="true"]');
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", card.dataset.taskId); } catch (_) {}
  });
  board.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    board.querySelectorAll(".bcol-body.over").forEach((b) => b.classList.remove("over"));
    dragged = null;
  });
  board.querySelectorAll(".bcol-body[data-drop]").forEach((body) => {
    body.addEventListener("dragover", (e) => {
      if (!dragged || body.dataset.drop === dragged.dataset.status) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      body.classList.add("over");
    });
    body.addEventListener("dragleave", (e) => {
      if (!body.contains(e.relatedTarget)) body.classList.remove("over");
    });
    body.addEventListener("drop", (e) => {
      if (!dragged || body.dataset.drop === dragged.dataset.status) return;
      e.preventDefault();
      body.classList.remove("over");
      move(dragged, body.dataset.drop);
    });
  });

  // The detail pane's complete toggle is app.js's, and it only knows how to
  // repaint a `.trow`. On the board there is no row to repaint and the card
  // would keep sitting in the wrong column, so that one form goes back to a
  // full Mode A reload — the page it returns to is this board, correctly sorted.
  document.querySelectorAll('.detail form[action^="/tasks/"]').forEach((form) => {
    if (/\/complete$/.test(form.getAttribute("action") || "")) {
      form.setAttribute("data-native", "");
    }
  });
})();
