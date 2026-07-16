# Review: central write guard + host allowlist + security headers (#15 slice)

Queue entry: 2026-07-16 — a74eab1 (+ follow-up e50090d) — `app/security.py`,
`app/main.py`, `verify.py`, `verify_restore.py`, `docs/security-model.md` —
PR #43, branch `fix/15-b2-write-guard`.

## Process

- Adversarial pass: Codex (`codex exec`, gpt-5.6, standing brief
  `docs/reviews/review-prompt.md` applied by file reference).
- Second independent pass: Opus subagent pointed at the same brief, reviewing
  the combined a74eab1+e50090d state (it ran long — a first attempt appeared
  dead and a retry was spawned; the retry delivered after this report was
  first committed, so its findings were converged in a follow-up commit).
- Correctness half: Claude (this file's convergence; built the change, then
  re-reviewed the combined diff for lifecycle/parsing/ASGI-scope issues).

## Codex verdict

One Low finding; no blocking issue for the documented direct-loopback HTTP
deployment.

**W1 (Low, confirmed) — origin comparison ignored the scheme.** The guard in
`a74eab1` compared only `(hostname, port)` between `Origin` and `Host`, so
`Origin: https://localhost` was accepted against the http app when default
ports were implicit, and non-http(s) or non-serialized `Origin` values could
match too. Confirmed locally against a throwaway uvicorn before fixing.

**Fixed in e50090d:** the guard now requires each `Origin` value to be a
serialized http(s) origin — no userinfo, path, query, or fragment — equal to
`(scope scheme, hostname, effective port)` with default ports normalized.
Three regression checks added (scheme mismatch, default-port normalization,
non-serialized origin); verify 361/361.

Codex also reported `verify.py` stalling inside `TestClient` in its sandbox.
Refuted as environmental (the known sandbox artifact): the suite passes
locally (361/361) and in CI on the PR head.

## Regression check on earlier findings

Codex re-checked the terminal gate's protections (loopback peer, loopback
Host, duplicate-Origin, exact authority, stale writer/control, attach/reaper)
— unchanged, no regression. The middleware refuses only untrusted-Host
handshakes and never accepts a WebSocket itself; `/terminal/ws` keeps the
stricter gate.

## Opus second pass

Verdict: no High or Medium findings; verify 361/361 reproduced; direct
probes of the guard (userinfo smuggling `http://host:port@evil.com`, scheme
mismatch, wrong port, suffix-host `testserver.evil.com`, `null`, path/query
variants, odd schemes) all rejected; same-origin case-insensitive and
default-port cases allowed. Confirms W1's fix is a real tightening and that
`app/terminal.py` (F1–F4, env-rename) is untouched — no regression.

Four Low/informational notes, converged as follows:

1. `scope["scheme"]` is not proxy-aware: behind a TLS proxy that doesn't
   forward the scheme, legitimate https same-origin writes would 403.
   Fail-closed (breakage, never bypass) and out of the documented
   no-proxy-headers posture — accepted, no change.
2. The guard covers unsafe methods only; a future mutating GET would sit
   outside it. Real invariant, previously undocumented — now stated in the
   `app/security.py` docstring (GET/HEAD must stay side-effect-free).
3. No-Origin + no-Sec-Fetch-Site requests are allowed by design (non-browser
   loopback clients). Already documented as defense in depth, not a CSRF/auth
   substitute — no action.
4. Host userinfo is silently dropped by parsing (`Host: evil.com@localhost`
   → trusted `localhost`). Not reachable from a browser (Host is a forbidden
   header; DNS rebinding presents the navigated domain without userinfo);
   the terminal gate parses identically. Accepted, no change.

Plus one doc nit — the docstring said a bad-Host WS handshake gets "403"
while the code closes with 1008 (HTTP gets 400) — fixed with item 2 in the
same follow-up commit.

## Correctness half (Claude)

- Middleware sees every route including mounts; the deliberately unguarded
  probe route in `verify.py` proves a forgotten per-route check can no longer
  reopen the CSRF gap.
- WebSocket scope never reaches the write guard (`scope["method"]` guarded by
  scope type); refusal on a WS handshake sends `websocket.close` pre-accept,
  not an HTTP response.
- Header injection touches only `http.response.start` and uses setdefault
  semantics, so the lesson-preview CSP (`frame-ancestors 'self'`) survives.
- Host parsing uses `urlsplit("//" + host)` (bracketed IPv6 correct), the
  reason Starlette's `TrustedHostMiddleware` was not used.

## Outcome

Entry drained. W1 fixed in e50090d; the Opus pass's two documentation gaps
fixed in the follow-up commit; the remaining Lows are accepted posture,
recorded above. Deploy gate clears for this surface once the PR merges.
