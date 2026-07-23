# Attempt body stream cap review

**Date:** 2026-07-23

## Self-parameterization

- **Scope:** queue entry #59, landed via merge commit `06e4674`, whose tree is
  byte-identical to reviewed branch head `9d9b8ee`. The review covered the
  exact `c125534..9d9b8ee` change in `app/main.py`, `verify.py`, and queue
  bookkeeping, plus the shared bounded-body reader and the attempt handler's
  route, security-middleware, lesson lookup, and attempt-service callees.
- **Starting HEAD:** `62f8afa2182a4e613e72157e3df84f6c63e1967e`
  on clean `main`, equal to `origin/main` and descending from the caller's
  required starting commit.
- **Report file:**
  `docs/reviews/2026-07-23-attempt-body-stream-cap-review.md`.
- **Prior reports reconciled:** the 2026-07-20 attempt-backend report and the
  latest D5/F4/F5 reports touching the attempt surface. In particular, this
  entry is the accepted Low follow-up A1; A2 is the separate projection-cost
  queue entry. D5 L1 remains mitigated and D5 L2/L3 remain resolved.
- **Validation baseline:** host `python verify.py` — **770 passed, 0 failed**;
  host `python verify_restore.py` — **28 passed, 0 failed**.

## Context and method

The landed branch tree and its complete request-admission diff were read
together with both public aliases, `read_capped`, the central unsafe-method
perimeter, lesson lookup, and `attempts.record_attempt`. The full changed
direct-ASGI regression was checked against Starlette's stream contract. Earlier
PR-bot conclusions were treated as closed claims, not re-counted as findings.

Adversarial tracing covered missing, malformed, negative, over-cap, dishonest,
and multi-chunk `Content-Length` cases; content-type and JSON refusal ordering;
single-chunk and cumulative chunk bounds; early response without draining the
remaining stream; alias parity; threadpool handoff; cache behavior; and whether
the change altered authority, persistence, projection, rate, or listener
semantics.

The threat model is the documented direct-loopback, single-worker,
unauthenticated single-user deployment. No live service, private instance,
runtime bundle, browser state, or real learner data was read or changed.

## Findings

No Critical, High, Medium, Low, Info, or other finding was found.

The application no longer joins an unbounded delivered body before enforcing
the attempt envelope. `read_capped` counts each ASGI stream chunk before
extending the retained byte array and raises as soon as the cumulative size
would exceed 256 KiB. The shared handler converts that exception to the
existing no-store 413 response. Both id and slug aliases call this handler.
Negative declared lengths now receive the existing no-store
`invalid-request` 400 before the receive callable is touched.

The remaining parser-independent behavior is intentional and bounded:
`Content-Length` remains an early refusal/required framing contract, while
delivered bytes are the memory-admission authority. A dishonest smaller
declaration cannot admit more than 256 KiB. An in-cap body follows the same
content-type check, JSON grammar, threadpool handoff, lesson lookup, and
attempt-service path as before.

## Confirmed protections

- The direct-ASGI regression makes parser choice irrelevant and proves that
  the fifth 64 KiB chunk receives 413 while the final three chunks are never
  requested, for both aliases.
- Declared bodies above the cap and negative declared lengths fail before body
  consumption. Malformed JSON, non-object JSON, unsupported media types, and
  missing lengths retain distinct responses and no-store caching.
- The central trusted-Host and unsafe-method origin middleware is unchanged.
  Same-origin parent requests and deliberate origin-less local clients retain
  their prior posture; opaque-origin iframe writes still fail at the perimeter.
- Attempt authority, idempotency, validation, rate accounting, SQLite/event
  transactions, projection format, and projection failure behavior are
  unchanged by this entry.
- Merge commit `06e4674` and branch head `9d9b8ee` resolve to the same tree
  `faffb10ce8f718ae3f1ea711202958395c291381`; no ephemeral preview hash is
  needed or treated as repository evidence.

## Prior-condition reconciliation

- **Attempt-backend A1 — RESOLVED.** Delivered-byte admission is now enforced
  while consuming the ASGI stream, with a parser-independent multi-chunk
  regression and negative-length refusal. The application no longer buffers
  the complete dishonest body before returning 413.
- **Attempt-backend A2 — STILL OPEN in queue entry #58 at this closing tree.**
  This body-admission entry does not claim to change projection locking,
  append verification, or reconciliation cost.
- **Bridge-runtime D5 L1 — REMAINS MITIGATED; D5 L2/L3 — REMAIN RESOLVED.**
  The navigation-stable `WindowProxy` residual remains documented. Versioned,
  byte-bound page serving and record-time parent/server validation are
  unchanged.
- **D3 brief/bridge and attempt-authority protections — REMAIN RESOLVED.**
  Authenticated port use, fresh request ids, server-derived lesson/page
  identity, untrusted-answer treatment, transaction atomicity, idempotency,
  record-time manifest checks, and private projection handling are unchanged.
- **Deployment and terminal conditions — retain their prior dispositions.**
  The direct/no-forwarded-header condition remains mitigated by direct
  loopback. Terminal-opt-in remains open/accepted only for the deliberately
  plain owner shell. No listener, terminal, sandbox, or live-runtime path
  changed.

## Closing verification

- `git diff --check c125534..9d9b8ee` — passed.
- Merge/tree identity — `06e4674^{tree}` equals `9d9b8ee^{tree}`.
- Host `python verify.py` at the starting application tree — **770 passed,
  0 failed**, including both parser-independent attempt-alias stream tests.
- Host `python verify_restore.py` — **28 passed, 0 failed**.
- No fix commit was required; this drain consumed **0 of 10** cycles.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment, subject to the
repository-wide queue gate.** This entry has **0 Critical, 0 High, 0 Medium,
0 Low, and 0 Info** findings and no open finding. Attempt-backend follow-up
**A1 is resolved**. Wider, proxy-adjacent, or multi-user deployment remains
**NO**. Queue entry #59 may move to Done, but the repository is not yet
restart-approved while entry #58/A2 remains Pending. A live restart remains
owner-only and was not performed.
