// board.js — the kanban board's drag & drop (#53). Deliberately its own file
// next to dnd.js rather than a branch inside it: the calendar's drag moves an
// event to a day, this one moves a task to a column, and the only thing they
// share is the HTML5 DnD shape.
//
// Framework-free progressive enhancement. Every move here is also a plain POST
// form on the card (the ←/→ buttons), so with this file blocked or broken the
// board still works; errors put the card back where the server last said it
// was, and anything this file cannot keep exact resolves on the next render.
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
  const TODAY = board.dataset.today || "";
  const ORDER = Array.from(board.querySelectorAll(".bcol")).map((c) => c.dataset.status);
  const LABEL = {};
  board.querySelectorAll(".bcol").forEach((c) => {
    const name = c.querySelector(".bcol-name");
    LABEL[c.dataset.status] = name ? name.textContent.trim() : c.dataset.status;
  });

  // What the detail pane, if one is open, was rendered from. A move that
  // changes THAT task's completion makes the pane lie (its toggle would
  // advertise the transition it no longer performs), so the page is re-rendered
  // rather than patched in two places.
  const selected = (() => {
    const form = document.querySelector('.detail form[action^="/tasks/"]');
    const hit = form && /\/tasks\/(\d+)\//.exec(form.getAttribute("action"));
    if (!hit) return null;
    const box = document.querySelector(".detail .checkbox");
    return { id: hit[1], completed: !!box && box.classList.contains("on") };
  })();

  /** Recount every column header from what is actually in the DOM. */
  function recount() {
    board.querySelectorAll(".bcol").forEach((col) => {
      const n = col.querySelectorAll(".bcard").length;
      const badge = col.querySelector("[data-count]");
      if (badge) badge.textContent = n;
    });
  }

  /** Drop what a capped column (Done) no longer shows, so a completion does
   *  not leave the server's limit + 1 cards on screen. Only a CONFIRMED
   *  arrival may evict: an eviction cannot be undone, and a move that turns
   *  out to be refused must not cost the board a card it never got back. */
  const capOf = (body) => (body ? parseInt(body.dataset.limit || "0", 10) : 0);

  function trim(body) {
    const limit = capOf(body);
    if (!limit) return;
    const cards = body.querySelectorAll(".bcard");
    for (let i = limit; i < cards.length; i += 1) cards[i].remove();
  }

  /** A capped column showing its full quota may be hiding more behind it, so a
   *  card leaving it takes the board out of step with what the server would
   *  render — there is a replacement to show and only the server knows it. */
  function isCappedFull(body) {
    const limit = capOf(body);
    return limit > 0 && body.querySelectorAll(".bcard").length >= limit;
  }

  /** Put a card in a column. Done reads newest-first, so a completion goes to
   *  the top there; the open columns are sorted by priority and due date,
   *  which is the server's rule and stays the server's — a card dropped into
   *  one sits at the end until the next render rather than being re-sorted
   *  here by a second copy of that rule. Insertion stays before the empty-state
   *  line so the CSS that hides it (`.bcard ~ .bcol-empty`) keeps holding. */
  function place(card, status) {
    const body = bodyOf(status);
    if (!body) return false;
    if (status === "done") body.insertBefore(card, body.firstElementChild);
    else body.insertBefore(card, body.querySelector(".bcol-empty"));
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
    // "Overdue" is a warning about an open task; completing it answers the
    // warning, and reopening it late brings it back.
    const due = card.querySelector(".due");
    if (due && !due.classList.contains("today")) {
      const late = !completed && !!due.dataset.due && due.dataset.due < TODAY;
      due.classList.toggle("overdue", late);
    }
  }

  /** Show a card exactly as the server last stored it: right column, right
   *  arrows, right completed look — and, when the open detail pane was
   *  rendered from the other completion state, re-render the page, since the
   *  pane's toggle would otherwise advertise the wrong transition. Used for a
   *  confirmed move and for the rollback of a refused one, so a card can never
   *  come to rest showing a state nobody stored. */
  function settle(card, status, completed) {
    place(card, status);
    trim(bodyOf(status));   // before the count, or the header reports the evicted card
    recount();
    retarget(card, status, completed);
    if (selected && selected.id === card.dataset.taskId && completed !== selected.completed) {
      location.reload();
    }
  }

  /** Back to the last state the server acknowledged. `status='done' ⇔
   *  completed` is the server's own invariant, so the column says everything. */
  function rollback(card) {
    const status = card.dataset.serverStatus || card.dataset.status;
    settle(card, status, status === "done");
  }

  // One writer per card. A second gesture on a card whose request is still in
  // flight is remembered, not raced: the in-flight writer picks it up when it
  // returns, so the last gesture is the one that ends up stored — two POSTs for
  // the same task can never be outstanding at once and finish out of order.
  const pending = new Map();

  async function move(card, status) {
    const id = card.dataset.taskId;
    if (!id || status === card.dataset.status) return;
    const leaving = card.dataset.status;
    const leavingFullCap = isCappedFull(bodyOf(leaving));
    place(card, status);                       // optimistic: the gesture shows at once
    recount();
    if (pending.has(id)) { pending.set(id, status); return; }
    pending.set(id, status);
    let target = status;
    for (;;) {
      const res = await post("/tasks/" + id + "/status",
        { status: target, return_to: location.pathname });
      if (!res.ok) {
        pending.delete(id);
        rollback(card);        // including back to a move that already succeeded
        if (leavingFullCap && card.dataset.status !== leaving) {
          location.reload();   // an earlier move DID take it out of the full column
          return;
        }
        toast(res.error || "could not move");
        return;
      }
      card.dataset.serverStatus = res.status;
      const queued = pending.get(id);
      if (queued === res.status) {
        pending.delete(id);
        settle(card, res.status, res.completed);
        if (leavingFullCap && res.status !== leaving) {
          location.reload();   // let the server show what was hidden behind it
        }
        return;
      }
      target = queued;                         // a newer gesture arrived while we waited
    }
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

  // Remember where the server last had each card, so a refused move puts it
  // back there instead of wherever the failed gesture dropped it.
  board.querySelectorAll(".bcard").forEach((card) => {
    card.dataset.serverStatus = card.dataset.status;
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
