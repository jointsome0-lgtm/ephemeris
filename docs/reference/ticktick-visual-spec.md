# TickTick Premium — Visual Spec (live study, 2026-06-06)

Purpose: capture the **exact** visual design system of TickTick's premium web app
(`ticktick.com/webapp`) so Activity Ledger can be re-skinned to "practically the
same UI/UX as premium" (user request 2026-06-06). Tokens were read from the live
app's computed CSS via Playwright; screens were screenshotted at 1440×900.

> This supersedes the §7.3 "do not match TickTick visuals" guardrail **for the
> user's own personal build**, per their explicit 2026-06-06 instruction. We still
> recreate the look in **our own CSS** — we do not copy TickTick's stylesheet,
> logo, or proprietary illustrations verbatim.

## 1. Design tokens (measured)

Theme: `white-theme` (light). `body.class = "sidebar-focus tick webkit en_US"`.

```
/* core */
--primary:            #4772fa   (rgb 71,114,250)   /* signature blue */
--primary-hover:      #6c8efb
--primary-active:     #3e60cd
--primary-disable:    #b5c7fd
--bg-main:            #ffffff   /* content + detail + sidebar body */
--bg-rail:            #f5f5f5   /* far-left 50px icon rail */
--bg-card:            #fafafa   /* stat cards / inputs (light) */
--front:              #191919   (rgb 25,25,25)      /* primary text */
--front-40:           rgba(25,25,25,0.4)            /* muted / placeholder */
--front-30:           rgba(25,25,25,0.3)            /* section headers, counts */
--front-06:           rgba(25,25,25,0.06)           /* selected/hover row bg */
--border:             #eeeeee   (gallery)
--shadow:             0 5px 18px rgba(0,0,0,0.2)
--cancel-hover:       #f4f4f4
--cancel-active:      #e8e8e8

/* priorities (task checkbox + matrix quadrants) */
--prio-high:          #e13e39   (red)
--prio-medium:        #faa80c   (amber)
--prio-low:           #4772fa   (blue, == primary)
--prio-none:          rgba(25,25,25,0.4) (gray)

/* semantic */
--green:    #0cce9c   --turquoise: #10c3ee   --orange: #ff5900
--warn-red: #e03131   --pro-gold:  #ff8e0a   --highlight: #fbc140
```

Typography: system-ui stack (`system-ui, -apple-system, "Segoe UI", Roboto,
Helvetica, Arial, sans-serif`, emoji fallbacks). **Base 14px.** Text `#191919`.

| Element | size / weight / color |
|---|---|
| Page title (e.g. "Inbox") | 20px / 600 / #191919 |
| Section header ("Lists", "Feature Modules") | 12px / 700 / rgba(25,25,25,.3) |
| Task title | 14px / 400 / #191919 |
| Counts / meta | ~12–13px / 400 / rgba(25,25,25,.3–.4) |
| Date meta (today) | blue #4772fa; overdue = red |

Radii: selected nav item **10px**; cards ~8–12px; checkboxes ~3–4px (rounded
square, **not** circular). Task row height **40px**. Add-task bar text is muted.

## 2. Layout (the 4-pane shell, desktop ≥ ~1000px)

```
[ icon rail 50px ][ list sidebar 305px ][ content (flex) ][ detail (flex, opens on select) ]
   #f5f5f5            #ffffff               #ffffff           #ffffff, 1px #eee left border
```

- **Icon rail (50px, #f5f5f5):** avatar (top, with gold pro dot) → nav icons →
  bottom: sync, bell, help. Icon-only (no labels). Active icon = blue glyph.
  Rail order (hrefs): `#p/inbox/tasks` Tasks · `#c/all/calendar/m` Calendar ·
  `#focus` Focus · `#m/all/matrix` Matrix · `#q/all/habit` Habit ·
  `#countdown` Countdown · `#s` Search.
- **List sidebar (305px, white):** smart lists **Today / Next 7 Days / Inbox**
  (📅 🗓️ 📥, right-aligned gray count) → **Lists** header + user lists (emoji +
  count) → **Filters** (desc text) → **Tags** (desc text) → divider →
  **Completed** (☑) · **Trash** (🗑). Selected/hover = rgba(25,25,25,.06), 10px radius.
- **Content:** header row [title 20/600] … [view/sort/⋯ controls]; then a full-width
  **"+ Add task"** bar; then collapsible **sections** (chevron + name + count) of rows.
- **Detail:** opens on selection; 1px #eee left border. Empty = faint illustration.

Calendar / Matrix / Focus hide the list sidebar and use the full width.

## 3. Screen-by-screen

Screenshots are local-only ignored reference captures under
`docs/reference/screenshots/tt-*.png`; do not commit them.

- **Tasks list** (`tt-inbox.png`, `tt-welcome-list.png`): rounded-square checkbox
  (priority-colored border) · emoji+title · faint note-page icon on far right when
  the task has a description. Sections: chevron + bold name + gray count. Completed
  section: dimmed, checked, in place.
- **Task detail** (`tt-task-detail.png`): top bar = [checkbox] · gray "📅 Due Date"
  pill · flag (priority) far right. Bold title (emoji), note text, attachments.
  Bottom bar = list tag · A (format) · 💬 comment · ⋯. Selected row in content gets
  rgba(25,25,25,.06) bg + drag handle (≡) + hover ⋯.
- **Today** (`tt-today.png`): smart-list with **Countdown** / **Habit** sections;
  date meta "Today" in blue on the right. (Matches our /today structure already.)
- **Habit tab** (`tt-initial.png`): header "Habit ⌄" + grid-toggle + `+` + ⋯; a
  **week strip** (Sun–Sat, today blue, per-day check ring, past days hatched);
  date chip "🎗 Jun 6 ✕"; habit rows = emoji avatar · name · "⚡N Day 🔥N Day" ·
  circular check ring on right. Selected row = **blue outline** (tasks use gray fill).
- **Habit detail** (`tt-habit-detail.png`): 4 stat cards (2×2, #fafafa): ✅ Monthly
  check-ins · ⚡ Total Check-Ins · 🎯 Monthly check-in rate · 🔥 Current Streak —
  each "label on top, big value + unit below". Monthly calendar (day number + small
  status ring below; today number blue). "Habit Log on {Month}". **Our §31 design
  already matches this** — needs only the light reskin.
- **Calendar** (`tt-calendar.png`): full-width month grid, 7×6; day number top-left
  (today = blue circle); events as colored pills (countdown=orange, tasks=blue,
  completed=muted+strike). Header: title + `+` + "Month ⌄" + ‹ Today › + ⋯.
- **Matrix** (`tt-matrix.png`): 2×2 quadrant cards (white, subtle border): ❶ Urgent &
  Important (red) · ❷ Not Urgent & Important (amber) · ❸ Urgent & Unimportant (blue) ·
  ❹ Not Urgent & Unimportant (green). Tasks inside as normal rows; empty = "No Tasks".
- **Focus** (`tt-focus.png`): "Pomodoro" + segmented [Pomo|Stopwatch]; big circular
  ring "25:00"; blue "Start" pill. Right: Overview (4 stat cards) + Focus Record.
```
