# UX Primitives — distilled for Activity Ledger

Status: v0.1 · Date: 2026-06-05
Source: distilled from `ticktick-ux-report.md` (TickTick web, disposable account, Playwright inspection).
Scope: **pattern-level** primitives only — no TickTick CSS/assets/wording. These are the reusable interaction shapes worth building, each mapped to a system-design.md section and a verdict.

Legend — **Verdict**: ✅ adopt · 🟡 adapt (changes needed for our 4-status model) · 🔭 later (post-MVP) · ⛔ avoid.

---

## P1 — Pinned quick-add bar ✅
A capture field fixed at the top of the daily list; focus → type → Enter creates an item without leaving the screen.
- **Why:** zero-navigation capture; keeps Today as the single hub.
- **Maps to:** §16.2 (Today), §17 (item mgmt). **Note:** MVP item-creation may live on an Items screen (§17); if Today gets quick-add, keep it to one line.

## P2 — Section grouping with counts ✅
List broken into labeled, collapsible sections, each showing a live count (`Habit 1`, `Completed 2`).
- **Why:** structure + progress sense without a separate screen.
- **Maps to:** §17 priority groups (`P0 Core / Extra / Background`). Use these as the section headers; show `done/total` per group.

## P3 — One primary affordance per row ✅ (load-bearing constraint)
Each row exposes exactly **one** obvious tap target (TickTick: the checkbox). Everything else is secondary.
- **Why:** speed + thumb accuracy; the reason TickTick check-in is 1 tap.
- **Maps to:** §16.2, §22.4 (<60s for 5–10 items). **This is the constraint P6 must satisfy.**

## P4 — Row anatomy: `[status] title …… [one right-meta]` ✅
Left status control, title, and a single right-aligned meta (date or tag). No more than one meta.
- **Maps to:** §16.2 row spec. Our right-meta = the note indicator or last-status glyph, not a date.

## P5 — Completed-but-visible, dimmed, reversible ✅
Done items stay in place under a dimmed, collapsible **Completed** group; un-tapping reverts.
- **Why:** progress visibility + "recovery, not shame" (§16.5); reversibility suits our append-only correction model (§14).
- **Maps to:** §14 (corrections via new events), §16.5. For us, *any* terminal status (done/light/skip/fail) moves the row to a "logged" state that's still visible and re-editable.

## P6 — Status entry for >1 outcome 🟡 (**the open question**)
TickTick is binary, so it uses one checkbox. We have **four** statuses (`full_done / light_done / skipped / failed`, §13) and must NOT break P3 by putting four labeled buttons on a 390px row.
- **Candidate patterns to prototype (pick via the 390px test, §22.4):**
  - **(a) Tap-to-complete + reveal:** primary tap = `full_done` (the common case, 1 tap); long-press / swipe / row-expand reveals `Light / Skip / Fail`.
  - **(b) Compact segmented control:** four **icon** targets (not words), each ≥44px, only if they fit without wrapping.
  - **(c) Tap → status sheet:** row tap opens a bottom sheet with the four choices (1 tap to open + 1 to choose).
- **Decision rule:** whichever keeps `full_done` at 1 tap AND passes §22.4 at 390px AND keeps all targets ≥44px. **Build a throwaway prototype before committing the Today template.**
- **Maps to:** §16.2, §16.4 (stay on Today after tap), §22.4.

## P7 — `light_done` has no analog ✅ (differentiator, guard it)
TickTick goal types are only "Achieve it all" (binary) or "Reach an amount" (quantitative) — **no minimum/partial state**. Our `light_done` (chain-preserving minimum) is a real differentiator.
- **Implication:** don't let P6's density pressure collapse `light_done` away; it's a core reason this product exists (§3 goals). Make it cheap to reach (one secondary gesture), not buried.

## P8 — Week-strip date navigation 🔭
Horizontal 7-day strip with per-day status dots for moving across recent days.
- **Maps to:** §15.2 (History). Good fit for History day-hopping; not needed on MVP Today.

## P9 — Streak/stat detail + monthly heatmap 🔭
Per-item detail view with stat cards (streak, totals, rate) + a calendar heatmap + per-check-in log notes.
- **Maps to:** §25 v0.2 (stats), §13 `daily_notes` (the per-check-in note already exists in our model). Defer visuals to post-MVP; the data model already supports it.

## P10 — Mobile nav: bottom tabs, not a drawer ✅ (diverge from web)
TickTick **web** collapses nav into a hamburger **drawer**; its **native** app uses **bottom tabs**. Bottom tabs win for thumb reach.
- **Decision:** keep Activity Ledger's planned **bottom nav** (Today / History / Items, §16.2). Do **not** copy the web drawer.

## P11 — Single-column reflow at 390px ✅
At 390px everything is one full-width column; no multi-column, no cramming multiple controls per row.
- **Why:** reinforces P3/P6 — the viewport itself forbids a 4-button row.
- **Maps to:** §16 mobile-first, §22.4.

---

## Anti-patterns / explicitly avoid ⛔
- **Copying visuals:** TickTick CSS, icon set, illustrations, marketing wording, exact spacing (§7.3).
- **Multiple competing affordances per row** at mobile width (violates P3; the trap P6 must dodge).
- **Hiding completed work entirely** (breaks progress visibility + our recovery ethos).
- **Web-style hamburger drawer on mobile** when bottom tabs serve our 3 destinations better.

## One-line summary for the coding agent
Build Today as: **pinned/secondary quick-add → priority sections with counts (P2) → rows with one primary tap = `full_done` (P3/P4) and a secondary gesture for `light/skip/fail` (P6) → completed items stay dimmed & reversible (P5) → bottom tabs (P10), single column at 390px (P11)**. Prototype P6 at 390px against the §22.4 speed test before locking the template.
