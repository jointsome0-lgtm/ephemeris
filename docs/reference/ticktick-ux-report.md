# TickTick UX Reference Report

Status: v0.1 · Date: 2026-06-05
Purpose: UX **reference only** for Activity Ledger (per system-design.md §7–§9). Not a clone study.
Method: Playwright-driven inspection of the TickTick **web** app at `ticktick.com/webapp`, desktop (~1280×720) and mobile (390×844) viewports, using a **disposable account** (system-design.md §28). No CSS/assets/icons/logos/proprietary text were copied; observations are interaction patterns only. Screenshots are local-only (gitignored) under `docs/reference/screenshots/`.

> ⚠️ Coverage is honest, not exhaustive. Inspected in depth: logged-out landing, app nav, **Today** list, **Habit** tracker (create → list → detail → check-in model), completion/done-state, mobile reflow + nav drawer. **Not** deeply inspected: task **detail drawer**, Calendar/Matrix/Focus views, swipe gestures, settings, search.

---

## 1. Answers to the §7.1 research questions

**What makes the Today screen fast for daily execution?**
- A single **pinned quick-add** bar ("Add task to 'Inbox'") at the very top — capture is always one tap away.
- **One primary affordance per row**: a left checkbox. Completing = **1 tap**. Nothing competes with it.
- **Section grouping with counts** (e.g. `Countdown 1`, `Habit 1`, `Completed 2`) gives structure without navigation.
- Completed items don't vanish — they drop into a dimmed, collapsible **Completed** group, so you see progress.

**How dense is the Today view? How are items grouped?**
- Comfortable, not cramped: full-width rows ~40–44px tall, generous whitespace. Grouped by **smart section** (Countdown / Habit / Completed here), each with a count and a collapse chevron.

**How are completed items displayed?**
- Filled checkbox, dimmed text, kept in-place under the **Completed** group with their source-list tag (e.g. `👋 Welcome`) on the right. Recovery-friendly: completion is reversible by un-checking; nothing is hidden or "lost."

**How many taps does a check-in require?**
- Task done: **1 tap** (checkbox).
- Habit check-in (Habit screen): select day in the week strip, then tap the row's status circle — effectively **1 tap** for "today" (today is preselected). Habits also appear in **Today** with a normal checkbox → **1 tap** there too.

**What's visible without opening details? What's hidden?**
- Visible on a row: **title · completion checkbox · one right-aligned meta** (a date like "Today", or a list tag). That's it.
- Hidden behind the detail drawer (not deeply inspected): notes, subtasks, priority, reminders, etc. The row stays clean by pushing everything secondary into the detail view.

**How does mobile navigation work?**
- The entire left navigation **collapses to a hamburger (☰)** that opens a **left slide-over drawer** (Today / Next 7 Days / Inbox / Lists / Filters / Tags / Completed / Trash / Habit / Countdown / Focus). The web app is **single-column** at 390px. Note: this is the **web** app — TickTick's **native** app uses a bottom tab bar instead.

**What can we copy conceptually? What should we avoid?** → §4 below.

---

## 2. Navigation model

**Desktop** (`ticktick-authed-inbox.png`, `ticktick-desktop-today.png`)
- A thin **icon rail** (far left): Tasks/Inbox · Calendar · Focus · Eisenhower **Matrix** · **Habit** · Countdown · Search.
- A secondary **list panel**: smart lists **Today / Next 7 Days / Inbox**, then **Lists** (user lists), **Filters**, **Tags**, then **Completed / Trash**, with **Upgrade to Premium** pinned at the bottom.
- A wide **content pane**; a fourth **detail pane** opens on the right when an item/habit is selected (seen in `ticktick-desktop-habit-detail.png`).

**Mobile 390px** (`ticktick-mobile-today.png`, `ticktick-mobile-nav.png`)
- Rail + list panel collapse into one **hamburger drawer**. Content is single-column. Same row anatomy as desktop.

---

## 3. Screen-by-screen notes

### 3.1 Logged-out landing (`ticktick-landing-public.jpeg`)
- A deep link to `/webapp/#p/inbox/tasks` redirects to the public marketing site when unauthenticated. The marketing hero embeds a **product mockup** of the 3-pane app (sidebar smart-lists → middle task list with "Add task" → right task detail with an Eggs/Milk checklist) — useful free signal, but it's a curated render.

### 3.2 Today / task list (`ticktick-desktop-today.png`, `ticktick-mobile-today.png`)
- Pinned quick-add bar; **section groups** (Countdown / Habit / Completed) with counts + collapse chevrons.
- **Row anatomy:** `[checkbox] title …………… [right meta]`. Right meta = date ("Today") **or** list tag ("👋 Welcome").
- **Habits surface here as checkable rows**, unifying tasks + habits in one daily surface.
- **Completed** group: dimmed, checked, in-place, with list tag retained.

### 3.3 Habit tracker (`ticktick-desktop-habits*.png`, `ticktick-desktop-habit-detail.png`)
- **Empty state:** "Develop a habit / Every little bit counts" + a **7-day week strip** date selector (Sat→Fri, today highlighted) with per-day circles.
- **List:** each habit = `icon · name · two streak stats (current streak / total)` + a row of weekly status dots. Check-in model: pick a day in the strip → toggle the row's circle. **Binary** (done / not-done).
- **Detail / stats:** stat cards (**Monthly check-ins, Total Check-Ins, Monthly check-in rate, Current Streak**) + a **monthly calendar heatmap** + a **"Habit Log"** for per-check-in thoughts/notes. Strong motivational/analytics layer.

### 3.4 Create-Habit flow (`ticktick-desktop-add-habit.png`)
- Fields: **Name** (+ emoji/icon), **Frequency** (Daily/…), **Goal**, **Start Date**, **Goal Days** (Forever/…), **Section**, **Reminder** (default 22:00, toggle), "Auto pop-up of habit log".
- **Goal** has exactly two types: **"Achieve it all"** (binary) or **"Reach a certain amount"** (quantitative with a counter). **There is no intermediate / minimum / "light" state.**

---

## 4. Implications for Activity Ledger (adopt / diverge)

**Adopt (conceptually):**
1. **Pinned quick-add** + **one primary affordance per row** — keep the common path to 1 tap.
2. **Section grouping with counts** (maps cleanly to Activity Ledger's `P0 Core Routine / Extra / Background` groups, §17).
3. **Completed-but-visible, dimmed, reversible** — fits the "recovery, not shame" goal (§16.5).
4. **A streak/stat detail view + heatmap** for later motivation (matches Activity Ledger's future stats roadmap, §25 v0.2). Not MVP.
5. **Week-strip date navigation** for moving across days (relevant to History, §15.2).

**Diverge / proceed with caution — these directly inform open design-review questions:**
1. **The four-status row `[Full][Light][Skip][Fail]` (§16.2) fights everything TickTick does.** TickTick keeps **one** affordance per row and is still careful at 390px; four labeled tap targets + title + note on one mobile row will be cramped (sub-44px targets or wrapping). Options to consider:
   - default tap = `full_done` (1 tap, the common case); reveal Light/Skip/Fail via a **secondary** gesture (long-press / swipe / expand), **or**
   - a compact **segmented control** (icons, not words), **or**
   - tap row → **status picker** sheet.
   This is the single biggest mobile-UX risk and deserves a prototype at 390px before committing.
2. **`light_done` is a genuine differentiator** — TickTick literally cannot express it (binary or quantitative only). Good. But it's also the source of the per-row complexity above; the value (chain-preserving minimum) must justify the density cost.
3. **Mobile nav:** TickTick **web** uses a hamburger drawer; Activity Ledger's planned **bottom nav** (Today/History/Items, §16.2) is *better* for thumb reach and matches TickTick's **native** app. Keep the bottom nav — it's the right call, not a copy.
4. **Speed budget:** binary completion in TickTick is 1 tap. Activity Ledger's richer model is inherently more taps; protect the "§22.4: 5–10 items in <60s" criterion by making the dominant status a single tap.

**Avoid (per §7.3):** copying TickTick's CSS/icon set/illustrations/wording/layout pixels. Everything above is pattern-level only.

---

## 5. Artifacts
Screenshots (local-only, gitignored) in `docs/reference/screenshots/` — 11 files:
`ticktick-landing-public.jpeg`, `ticktick-authed-inbox.png`, `ticktick-desktop-today.png`, `ticktick-desktop-habits-empty.png`, `ticktick-desktop-add-habit.png`, `ticktick-desktop-habits.png`, `ticktick-desktop-habit-detail.png`, `ticktick-desktop-habit-checked.png`, `ticktick-desktop-habit-checkedin.png`, `ticktick-mobile-today.png`, `ticktick-mobile-nav.png`.

See also `docs/reference/ux-primitives.md` for the distilled, implementation-facing primitives.
