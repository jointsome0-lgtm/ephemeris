# Activity Ledger — Design Review v0.1

**Subject:** `docs/system-design.md` (Draft v0.1, 1,396 lines)
**Date:** 2026-06-05
**Method:** Dual-model strategic-mix review — Claude (Opus 4.8) + Codex (GPT-5.5, xhigh). Single-owner synthesis by Claude; Codex as independent generator, cross-judge, panelist, and adversarial critic.
**Status:** Review only — no code written. This report recommended edits to the design doc; **all 12 medium + 8 low items have since been applied to `system-design.md`** (§16.4/§11 on 2026-06-05; the remainder on 2026-06-06) — see *Resolution status* at the end of §1. This version also incorporates a Codex adversarial-critique pass (§7).

---

## 1. Bottom line

The design is **strong, well-scoped, and build-ready.** Its non-goals, stop-loss rules, and the first-class `light_done` status are genuine assets most design docs lack.

After a full adversarial pass, **there are no high-severity blockers.** What remains are **twelve decisions worth recording in the doc** so the coding agent doesn't guess on things that matter (two of them shape the build enough to settle first), plus eight minor/hardening items.

| Tier | Count | Themes |
|------|-------|--------|
| 🔴 **High — blocker** | 0 | — |
| 🟡 **Medium — decide in the doc** | 12 | **T3 event-log role + atomicity · T4 check-in note-save & undo** (settle first); then T1 timezone · T2 history relabel · T5 status-tap mechanism · T6 export contract · T7 390px + note-save · T8 schema invariants · T10 TickTick research scoping · T12 0.0.0.0 exposure · T13 CSRF · T15 gitignore the DB |
| 🟢 **Low — hardening / nits** | 8 | T9 ordering · T11 SQLite concurrency · T14 stored-XSS · T16 backup integrity · T17 file perms · T18 stop-loss trigger · T19 status color-only · T20 history nav |

**Meta-finding (calibration).** Severity here is deliberately conservative. Both models independently rated the *other's* findings as inflated (Claude: 12/16 of Codex's too high; Codex: 22/39 of Claude's too high). A 2-model panel deflated 11 first-pass Claude "highs" to 2; the final Codex critique then showed those last two are bounded — SQLite stays authoritative (§18.2) and the daily flow is status-first (§16.1) — so the calibrated result is **no highs.** Read "medium" as *a real gap the build agent will likely resolve wrong, fixable by one explicit decision.*

### Resolution status (applied to `system-design.md`, 2026-06-06)

Every reviewed item has been folded into the doc:

| Theme | Landed in |
|------|-----------|
| T1 timezone | §13.3 (APP_TIMEZONE, server-side day, midnight test) |
| T2 history relabel | §15.2 (checkins-first, include inactive) + §15.3 (soft deactivate, `updated_at`/`deactivated_at`) |
| T3 event log | §14.1 (role + catalog + item events) + §16.4 (one transaction) |
| T4 note-save/undo, T5 status-tap | §16.4 (dual-mode write contract) |
| T6 export contract | §18.1 (events-replay + download behavior) |
| T7 390px row | §16.2 (4-status ≥44px row contract + non-color cue + collapsed note) · *build-time:* prototype vs §22.4 |
| T8 schema invariants | §13.1 (CHECK/FK/`payload_version`) + §13.3 (PRAGMAs, `user_version` migrations) |
| T9 ordering | §13.3 (`ORDER BY`) + §13.1 (group default fix, index) |
| T10 TickTick research | §7 + §23 (optional spike) + §28 (ToS preflight + stop rule + gitignore deliverables) |
| T11 concurrency | §13.3 (WAL + `busy_timeout`) |
| T12 bind exposure | §10.2 (two run modes + startup warning) |
| T13/T14/T16/T17 sec-ops | §20 (same-origin CSRF, autoescape-only, consistent backup, optional perms) |
| T15 gitignore DB | §9 (`data/*.sqlite*`, exports) |
| T18 stop-loss trigger | §24 (measurable trigger) |
| T19 status color-only | §16.2 (non-color cue) |
| T20 history nav | §15.2 (default date, validation, prev/next, empty state) |

**The only open action is build-time, not a doc decision:** prototype the T7 status row at 390px against the §22.4 <60s test before locking the Today template.

---

## 2. Methodology

A finding that survives one model is a hypothesis; one that survives the *other* model is a result. Author ≠ judge at every step:

1. **Independent generation.** Claude across 7 lenses (architecture, scope, data-model, correctness-flows, ux-mobile, security-ops, legal-ethical), blind to Codex. Codex in 2 passes (build-risk, architecture). → **39 + 16 = 55 raw findings.**
2. **Cross-model judging.** Each model's findings judged by an independent instance of the *other* model. No self-judging.
3. **Corroboration map.** 55 findings → **20 themes**; **10 raised independently by both models.**
4. **2-model panel** on the 8 themes where the models disagreed on severity or one refuted the other. Both ruled independently. **Unanimous on all 8** (zero splits).
5. **Single-owner synthesis** (this doc) by Claude → **adversarial critique by Codex** → this revision. The critique changed real things (see §7).

Raw artifacts (gitignored) in `docs/reviews/.work/`.

---

## 3. What's strong (keep these)

- **Scope discipline is exceptional.** §5 Non-Goals and §24 "Forbidden scope creep" name the exact temptations to refuse, and §24's stop-loss gives a concrete buildable floor (single `app.py`, one `today.html`). Both models flagged this as a top strength.
- **`light_done` as a first-class status (§13.2)** is the product's real differentiator. The TickTick UX research confirmed why it matters: TickTick's goals are *only* binary or quantitative — no partial/minimum state exists anywhere. This is the reason to build rather than keep using TickTick.
- **Clean source-of-truth boundary.** SQLite authoritative; Markdown/JSONL explicitly *derived* (§18.2, §19). Layering (§12) is the right altitude for "understandable, hackable, disposable" (§11).
- **Acceptance criteria are mostly measurable** (§22: usable at 390px, 5–10 items in <60s, persists in SQLite).
- **Anti-cloning guardrails are explicit** (§3, §7.3): no TickTick CSS/assets/icons/text, no committed credentials/cookies.

---

## 4. 🟡 Decide first (the two that most shape the build)

Both started the review as candidate "highs"; the adversarial pass bounded them to medium, but they still touch the core write path and should be settled before Milestone 1.

### T3 — The event log's role is undecided, and it can diverge from the tables
*Raised independently by both models (Claude ARCH-1, FLOW-1; Codex `event-table-dual-write-can-diverge`, `event-log-second-source-unclear`).* §1, §13.1, §14, §15.3, §16.4, §18.

**Issue.** §13.1 puts an `events` table inside SQLite and §14 says "every meaningful change should append an event"; §16.4 makes every status tap write **two** places — the `checkins` row *and* an event — with no stated transaction. The doc never decides whether `events` is replayable truth, an audit sidecar, or the export feed.

**Why it matters (bounded).** The authoritative tables stay correct — §18.2 makes SQLite the source of truth, so live History is fine. The harm is to the **derived export / event-memory**, which is the product's *stated long-term purpose* ("our own memory," agent-readable history, Markdown/Git — §3, §14): a non-atomic write (crash between the two inserts) leaves the export silently disagreeing with reality. That's why it's a real medium, not a nit — but not a high, because nothing the user sees in normal daily use is corrupted. *(Codex's critique correctly pushed back on the original "corrupts the ledger" framing.)*

**Decision for the owner.**
1. State the role in §14 — recommended: **"`events` is an append-only audit/derived feed; the typed tables are authoritative; JSONL export is generated from events."**
2. Wrap the table mutation **and** its event insert in **one SQLite transaction** (rollback on either failure) in the service layer.
3. Apply this to **all** "meaningful changes," not just check-ins: §15.3 item **add/edit/deactivate** must emit `routine_item_created/updated/deactivated` events too — *or* explicitly scope §14 to checkins + daily notes only. *(Added per Codex critique.)*
4. Document `item_title` in the event payload as an **immutable snapshot** (also resolves half of T2).

**Status — addressed:** §16.4 wraps the check-in row + event in one transaction; §14.1 now states the event-log role (append-only audit/derived feed; tables authoritative), the full event-type catalog including item create/edit/deactivate, and `payload_version`.

### T4 — Note persistence and undo on the check-in row are undefined
*Raised by both models (Claude FLOW-2/3/4; Codex `checkin-note-upsert-semantics-missing`).* §1, §4.1, §13.1, §13.2, §16.1, §16.4, §21.2.

**Issue.** `checkins.status` is `TEXT NOT NULL` (§13.1) and the §16.2 wireframe shows a `note...` field on every row, but the doc never says **how/when a note persists** (autosave? explicit Save? saved with the status tap?), and there is no defined way to return a row to "untouched."

**Why it matters (bounded).** The spec's flow is *status-first* — "mark status → optionally add note" (§1, §16.1) — and §21.2 includes "update status," so a mis-tap **is** recoverable by tapping the correct status; it is not permanently stuck. *(This is Codex's correct refinement of the original "high, must add nullable status" framing.)* The residual real risk: if the build agent makes the status tap a separate POST that **doesn't carry the note field** (the form design is itself undefined — see T5), typed note text is silently lost on reload during the core daily ritual.

**Decision for the owner.**
- **Define note persistence:** an explicit per-note Save (or defined autosave) with visible save feedback, so closing the app never drops typed text. (This is the write-path half of T7.)
- **Decide whether a clear/undo-to-"untouched" exists.** Only make `status` nullable / add an "untouched" state **if** you actually want note-only rows or true clear — do **not** add it by default; the status-first flow doesn't require it.

**Status — addressed (§16.4 amended):** the doc now defines a single `POST /checkins` for status+note, note persistence in both Mode A (PRG) and Mode B (`fetch` autosave-on-blur with a saved indicator), and clear/undo by re-tapping the selected status — all without making `status` nullable.

---

## 5. 🟡 Also decide in the doc

Each was judged real in cross-review and/or the panel (some raised by both models, some by one — see provenance tags); severity is the cross-examined value. Format: *issue → decision*.

**T1 — "Today" / `date` has no owning clock or day-boundary.** *(both models; panel: medium)* §13.1, §14, §16.4. `checkins.date`/`daily_notes.date` are bare TEXT and the only identity key, yet no rule names the clock. Since the date is server-rendered (§11), phone-vs-server isn't the bug; the realistic one is an agent defaulting to `utcnow()` on a UTC host, which misfiles the **00:00–02:59 local** window into the *previous* UTC date (with the +03:00 offset of §14's examples, evening times like 21:10 stay on the correct date — it's the after-midnight hours that flip). → **Declare a configured `APP_TIMEZONE`, server-authoritative; `date = date(now(APP_TIMEZONE))` in YYYY-MM-DD; offset-aware ISO timestamps; add a boundary test around local midnight (e.g. 23:59 / 00:01).**

**T2 — Renaming/deactivating an item rewrites or hides past history.** *(both models; panel: medium)* §13.1, §15.2, §15.3. `checkins` stores only `routine_item_id`, so History must JOIN the live row — a rename relabels every past day, and an `active=1` filter copied from Today would hide a deactivated item's past check-ins. The `date/status/note` signal is never corrupted; the displayed labels are. → **Specify History's contract: (a) show title/group as-of-check-in (snapshot onto the row/events) or explicitly accept current-name display; (b) History joins all linked items regardless of `active`.** Add `updated_at`/`deactivated_at` to `routine_items`; never hard-delete.

**T5 — "Stay on Today" after a tap is undefined for a no-JS stack.** *(both models; panel: medium — vanilla PRG suffices, HTMX is not required)* §11, §16.2, §16.4. PRG-vs-plain-POST and one-form-per-item-vs-one-big-form are unspecified; the wrong guess re-submits/duplicates events on refresh or clobbers other rows. → **Specify: one small `<form>` per item → `POST /checkins` → 303 redirect to `/today#item-{id}` (prevents re-submit, preserves scroll); per-item note and daily note are separate POSTs.** Note scroll resets on full reload; accept for MVP or name HTMX as the mitigation if §22.4 fails.

**Status — addressed (§16.4 amended):** specified — one `<form>` per item → 303 PRG → `/today#item-{id}` (Mode A) and a `fetch` row-swap of the shared Jinja row partial (Mode B).

**T6 — JSONL export contract is ambiguous.** *(both models; panel: medium. The export-UI "contradiction" with §24 was **rejected** — §24 is a gated stop-loss fallback, not a co-binding rule)* §15.4, §18.1, §23. §18.1 pins a pure event-stream shape while §15.4/§23 say "events + checkins + daily notes" — sources that can't share one line-shape without a discriminator; no `record_type`/`schema_version`/ordering exists for the designated durable backup layer. → **Pick one: (a) export = events replay (reword "include checkins/daily notes" as event coverage), or (b) a snapshot of all three with per-line `record_type` + `schema_version` + stated ordering, read in one transaction. Also define whether `POST /export/jsonl` streams a download to the phone or only writes the server file** (download behavior is currently unspecified, and a server-only file leaves the Samsung client no way to retrieve it).

**T7 — 390px four-status row + note-save are unproven against the speed goal.** *(both models; panel: medium. Claude's "self-contradiction" framing was **rejected** — it leaned on external UX docs — but the feasibility concern is real and is what Codex's own mobile findings raise)* §16.2, §16.4, §22.4. The wireframe puts `[Full][Light][Skip][Fail]` **plus** an always-visible note on every row at 390px, with no tap-target size or note-save model, while §22.4 demands 5–10 items in <60s. Corroborating external evidence: TickTick keeps strictly one primary affordance per row and still reflows to single column at this width (`docs/reference/ux-primitives.md` P3/P6). → **Specify (a) the note-save mechanism (shared with T4) and (b) one 4-status entry pattern with a stated ≥44px target, rather than committing the literal four-equal-button row. Prototype at 390px against §22.4 before locking the Today template.**

**T8 — Schema invariants and migrations live only in prose.** *(both models; medium, driven by FK enforcement)* §13.1, §13.2, §25. `status` has no `CHECK` (a typo'd status persists silently and renders as an un-styled row), `active` no `CHECK(IN (0,1))`, titles can be empty, and **`PRAGMA foreign_keys` is OFF by default** so the declared FK is documentation only. No `user_version`/migration path though §25 plans schema changes. → **Add `CHECK` constraints (status set, `active IN (0,1)`, non-empty title); set `PRAGMA foreign_keys=ON` on every connection; set `PRAGMA user_version` + check on startup for idempotent migrations.** All zero-dependency. (FK enforcement is the medium; CHECK/migrations are individually low but cheap.)

**T10 — Logged-in TickTick research is under-fenced (legal + scoping).** *(both models; panel: medium — already partly guarded by §7.3/§9/§28)* §7–§9, §23, §28. Automated *logged-in* Playwright capture may exceed TickTick's ToS, and the doc has no ToS preflight or stop condition; §23 also lists it inside the MVP milestone chain while §22.12 says "no external service required." → **Fence it: declare the UX spike *optional and not MVP-blocking*; require a ToS preflight + hard stop rule before any authenticated automation (prefer logged-out/marketing/manual observation; disposable account only if login is truly needed); extend the no-PII/paraphrase rule and `.gitignore` to the committed `ticktick-ux-report.md`/`ux-primitives.md`.** *(This review's own UX research already followed these mitigations.)* *(Note: the earlier-claimed "§7.3 forbids the DOM, but research needs it" tension was **withdrawn** — §7.2 explicitly permits accessibility-tree/locators for research; §7.3 bans the **product** depending on TickTick's DOM, which is consistent.)*

**T12 — The documented run command exposes a no-auth app to the whole LAN.** *(Claude-only — Codex did not surface this; panel: medium)* §10.2, §20, §22. §10.2's only run command binds `--host 0.0.0.0`, which is **required** for the phone-over-Wi-Fi acceptance path (§22), while §20 says "do not run on 0.0.0.0 on untrusted networks." The defect is the **unguarded default**: on café/hotel Wi-Fi a no-auth read/write app becomes reachable by everyone. → **Document two run modes — `127.0.0.1` for desktop-only use, and trusted-LAN `0.0.0.0` for Samsung — and colocate the §20 untrusted-network warning (ideally a startup warning when binding `0.0.0.0`) with the LAN command.** Do **not** make `127.0.0.1` the silent default, as that breaks the mobile path.

**T13 — No CSRF protection on no-auth state-changing POSTs.** *(Claude-only; both models medium on cross-review)* §15, §20. §15 defines several unauthenticated POST endpoints; §20's local-only model doesn't address a malicious page in the user's own browser POSTing to `http://<lan-ip>:8000`. → **Add a lightweight mitigation: same-origin/`Origin`-header check or a per-session form token; at minimum note the residual risk.** (Cheap; not "add auth.")

**T15 — The source-of-truth DB isn't gitignored (in the doc).** *(Claude-only; both models medium)* §9, §12, §19. §12 places `data/activity.sqlite` in the repo and §9's required `.gitignore` only ignores Playwright/screenshots — though §19 cautions against storing the main DB in Git *as the primary sync model*. → **Add `data/*.sqlite*` (and `-wal`/`-shm` sidecars) to the doc's required `.gitignore` (§9).** For **exports**, don't blanket-ignore: §19 explicitly plans "Git commit of exports" as backup/history, so decide deliberately between *local-only private exports* and a *committed sanitized archive path* — the live notes are private, but the backup intent is real. *(Recommendation corrected per Codex critique; the original over-quoted §19 and would have suppressed the intended export backups.)*

> **Stack note — clean TypeScript is available (user-confirmed, 2026-06-05).** Three mediums above (T4 note-save, T5 status-tap, T7 390px speed) exist *only because of the no-JS Jinja constraint*. A small amount of **framework-free TypeScript/JS as progressive enhancement** is the cleanest resolution and stays within the non-goals — §5/§11 forbid React/Next, **not** plain client-side JS (§11 already says "vanilla… optional HTMX later"):
> - **T5 / T7:** a status tap does `fetch('/checkins', …)` and swaps just that row → no full reload, no scroll reset, no refresh-resubmit, instant feel for the §22.4 <60s goal (removes the "accept scroll reset" compromise).
> - **T4:** the per-item note autosaves on blur via `fetch` with a visible "saved" indicator → typed text is never lost, no separate Save button.
> **Keep it tiny and additive over the PRG path** (the no-JS forms remain the fallback) so §24's "hackable/disposable" floor holds — ideally one small `.ts` file (or plain JS, no build step), not a toolchain. It does **not** change the server-side findings (T1, T2, T3, T6, T8) — those are correctness/data issues regardless of frontend.

---

## 6. 🟢 Low — hardening & nits

Real but minor; most are one-line fixes.

- **T11 — SQLite concurrency.** *(Claude-only)* Two supported clients on one SQLite with no `journal_mode`/`busy_timeout` can throw "database is locked." Cheap fix: `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`. (Codex deflated to low for a one-person LAN app.)
- **T9 — Ordering determinism.** *(both)* `sort_order` defaults to 0 → collisions → list jitter. Fix: `ORDER BY group_name, sort_order, id`. Because §17's groups are "Example groups" and §15.3 lets you *edit* group (so group strings are free text, not a fixed set), also consider an allowed-group list or trim/normalize on write to avoid near-duplicate sections.
- **T14 — Stored-content XSS.** *(Claude-only)* Notes/titles are rendered back; Jinja autoescape is on by default so live risk is low — just **don't disable autoescape / don't use `|safe`**, and re-check when Markdown export (future, §18.2) lands.
- **T16 — Manual-backup integrity.** *(Claude-only)* §19's "manual copy of `activity.sqlite`" doesn't say how to copy consistently under WAL. Note: use `sqlite3 .backup` / `VACUUM INTO`, not `cp` mid-write.
- **T17 — File permissions.** *(Claude-only; Codex ruled out-of-scope)* Owner-only perms on `data/` assume a shared host the §6 model excludes. Optional hardening.
- **T18 — Stop-loss trigger isn't measurable.** *(Claude-only)* §24 "if Today page is not usable quickly" has no threshold though §22.3/§22.4 provide exact ones. Bind the trigger to those (fails 390px or <60s after Milestone 1).
- **T19 — Status distinguishable by color alone.** *(Claude-only)* §16.5/§22.6 want clear status but specify no non-color cue. Add an icon/shape/label per status (accessibility + grayscale).
- **T20 — History prev/next underspecified.** *(Claude-only)* §15.2 promises navigation but no default date, date math, empty-state, or future-date rule. Define them.

---

## 7. Cross-model analysis

**Corroboration (highest confidence).** 10 of 20 themes were raised *independently* by both model families (T1–T10) — almost certainly real.

**Single-model catches (the mix paid off both ways).**
- *Claude-only:* the entire security-ops/a11y surface — T12 (0.0.0.0 LAN exposure), T13 (CSRF), T14 (XSS), T15 (gitignore the DB), T11 (concurrency), T18/T19/T20.
- *Codex-only framing:* "the single riskiest decision is logged-in TickTick research in the MVP" sharpened T10's scoping.

**Refutations during cross-judging.** Codex overturned three Claude findings: **UXM-1** (the four-button method *is* specified in §16.2/§16.4 — the "contradiction" relied on external UX docs; substance survives in T7), **SCOPE-3** (§23's Milestone 5 can coherently be a post-hoc UX pass), **SEC-6** (file perms assume a multi-user host outside §6 → now T17, low).

**What the final Codex critique changed in this report (transparency).** The critique was run against this synthesis and corrected it:
- **Severity:** both remaining "highs" (T3, T4) were bounded to **medium** — T3 because SQLite stays authoritative (§18.2) so only the derived export diverges; T4 because the status-first flow (§16.1) + "update status" (§21.2) make note-loss contingent and mis-taps recoverable. → final tier: **0 high**.
- **Factual fixes:** corrected the T1 timezone example (the bug is the after-midnight window, not the evening); **withdrew** the T10 "DOM-dependency" tension (a misread of §7.2/§7.3); corrected the T9 "fixed group set" claim; softened the T6 "can't retrieve" to "unspecified."
- **Recommendation fixes:** T12 no longer defaults to `127.0.0.1` (that would break the §22 phone path); T15 no longer blanket-ignores `data/exports/` (that would suppress §19's intended export backups); T3's atomicity rule now extends to item-mutation events.

**Severity calibration.** Both models judged the other's severities inflated; the panel then agreed unanimously on all 8 contested themes; the critique deflated the last two highs. The 0-high / 12-medium / 8-low result is the fully cross-examined outcome.

---

## 8. What this review deliberately did **not** flag

Per the doc's stated non-goals, these are intentional and were **not** treated as defects: no React/SPA (§11); no auth / single-user / local-Wi-Fi-only (§5, §20); SQLite-only, no Postgres/Redis/Docker/VPS/cloud (§5, §11); no notifications/gamification/charts/AI/calendar (§5); "understandable, hackable, disposable" as a value (§11); Markdown export, weekly review, stats, PWA, basic-auth/VPS, paths/Story/Atlas as explicitly future (§25, §26); manual backup as a chosen MVP tradeoff (§19). Both models were instructed to mark any finding that merely objected to these as out-of-scope.

---

## 9. Pre-build punch list (recommended doc edits, in priority order)

The doc is buildable today; these stop the agent from guessing on what matters.

1. **[T3]** §14: declare `events` an audit/derived feed; one transaction per change; emit item-mutation events (or scope §14 to checkins+notes).
2. **[T4]** §16.4: define note persistence (Save/autosave + feedback); decide if clear/undo exists (nullable status only if note-only rows are wanted).
3. **[T1]** §13.1/§16: declare `APP_TIMEZONE`, server-authoritative day boundary; ISO timestamps; midnight boundary test.
4. **[T7]** §16.2: pick one 4-status entry pattern with ≥44px targets + the note-save model; prototype at 390px vs §22.4.
5. **[T2]** §15.2: History renders as-of-check-in labels and includes inactive items (no `active=1` filter in History).
6. **[T5]** §16.4: per-item form → 303 PRG → `/today#item-{id}`; notes are separate POSTs.
7. **[T6]** §18.1: choose one JSONL contract (events-replay *or* discriminated snapshot) + download-vs-file behavior.
8. **[T8]** §13.1: `CHECK` constraints, `PRAGMA foreign_keys=ON`, `user_version` migrations.
9. **[T12]** §10.2: two run modes; `0.0.0.0` opt-in with the §20 caveat colocated (don't break the phone path).
10. **[T10]** §23/§28: fence TickTick research as optional, ToS-preflighted, non-MVP-blocking.
11. **[T13, T15]** §15/§9: same-origin/token CSRF check; add `data/*.sqlite*` to the required `.gitignore` (decide export privacy vs §19 backup).
12. **[Low tier]** fold T9/T11/T19/T20 one-liners in opportunistically.

---

## 10. Provenance & artifacts

All intermediate outputs are in `docs/reviews/.work/` (gitignored):
- `claude-findings.json` (39 findings, 7 lenses) · `codex-build-risk.json` + `codex-architecture.json` (16 findings)
- `claude-verdicts-on-codex.json` · `codex-verdicts-on-claude.json` (cross-model judging)
- `corroboration-map.json` (55 → 20 themes) · `panel-input.json` · `panel-claude.json` · `panel-codex.json` · `panel-final.json` (2-model panel, unanimous)
- `codex-critique.json` (adversarial critique of this report) · `final-themes.json` (final severity table)

Models: Claude Opus 4.8; Codex GPT-5.5 @ xhigh reasoning. UX evidence: `docs/reference/ticktick-ux-report.md`, `docs/reference/ux-primitives.md`.

*Status: Claude-owned synthesis, revised after Codex adversarial critique. Review complete.*
