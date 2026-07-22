# Lesson artifact and run endpoints (phase F)

Status: the editor half is frozen with phase F1 and the run API with phase F4.
This is the HTTP contract used by the parent Learn runtime; the lesson document
itself has no network access. Manifest authority is
[learn-bundle-spec.md §4.4](learn-bundle-spec.md), and the child-facing membrane
is [lesson-bridge-abi.md](lesson-bridge-abi.md).

The documented deployment is direct loopback, single user, single worker, and
has no auth. Unsafe methods remain behind the central B2 origin guard. A bridge
port is routing, never authority: every request re-reads the manifest and
derives the file path from its declared `block_id`; no client request contains
a path.

## Artifact routes

```
GET  /learn/lessons/{lesson_id}/blocks/{block_id}/file
POST /learn/lessons/{lesson_id}/blocks/{block_id}/file
GET  /learn/lessons/by-slug/{slug}/blocks/{block_id}/file
POST /learn/lessons/by-slug/{slug}/blocks/{block_id}/file
```

The by-slug forms are aliases with identical behavior. Every route uses the
pure bundle reader: it never creates a bundle, manifest skeleton, standard
directory, artifact root, or legacy copy. Existing preview reads keep their
historical behavior and are a separate surface.

### GET response

An existing strict-UTF-8 file (HTTP 200):

```json
{"ok": true, "exists": true, "content": "print('hi')\n",
 "file_rev": "sha256:…64 lowercase hex…", "size": 12}
```

A missing file is also 200:

```json
{"ok": true, "exists": false, "content": "", "size": 0}
```

`file_rev` hashes the exact raw bytes read from one no-follow descriptor. Raw
content is capped at 64 KiB. The same descriptor must identify a regular file
with one link; invalid UTF-8 is refused rather than replacement-decoded.

### Save request and response

POST is `application/json`, admitted from the request stream with a 512 KiB
cap:

```json
{"content": "print('hi')\n", "base_rev": "absent"}
```

For an existing file, `base_rev` is the `file_rev` returned by GET. A first
write uses the literal `"absent"`. There is no ETag or If-Match variant.

Success (HTTP 200):

```json
{"ok": true, "result": "saved", "file_rev": "sha256:…",
 "size": 12, "event_recorded": true}
```

`result` is `saved` or `unchanged`. Content-equal retries are `unchanged`
without a filesystem write, ledger event, or rate charge. Other saves compare
and publish under a per-bundle lock. Conflict exclusion is strict among
artifact-API writers. Direct terminal writers are detected when their change
is visible during compare or the final descriptor-identity check, but a
same-user direct write in the final publication window remains last-write-wins;
strict mediation of every bundle write is outside v1.

Publication walks or creates parents relative to a no-follow descriptor for
the lesson root, writes and fsyncs a mode-0600 temporary file in the pinned
destination directory, re-checks the current descriptor identity, replaces
fd-relatively, and fsyncs the directory. A block file deeper than four levels
below its declared artifact root is not writable through this API.

Each real save attempts one best-effort `lesson_artifact_saved` event after
the file is durable. Its payload is `lesson_uid`, `lesson_id`, `slug`,
`block_id`, manifest-relative `file`, `file_rev`, `size`, and boolean
`created`; it never contains file content. A crash or DB failure after file
publication can lose telemetry, and an identical retry remains `unchanged`,
so `event_recorded` makes that limitation observable. This path never writes
`attempts.jsonl`.

The in-process single-worker abuse damper permits 30 save attempts per lesson
per 60 seconds. Validation/conflict outcomes are charged because they consume
the manifest/filesystem path; `unchanged` and `rate-limited` are uncharged.

## Artifact refusal matrix

Every refusal is:

```json
{"ok": false, "error": "<code>", "detail": "…"}
```

`file-conflict` additionally carries the current `file_rev` (JSON `null` when
the file is absent). A 429 carries `Retry-After`.

| status | error | condition |
|--------|-------|-----------|
| 400 | `invalid-block-id` | route id does not match `blk_[a-z0-9]{4,32}` |
| 400 | `invalid-base-rev` | neither `absent` nor `sha256:` plus 64 lowercase hex |
| 400 | `invalid-content` | `content` is not a UTF-8-encodable JSON string |
| 400 | `invalid-json`, `invalid-request` | malformed/non-object JSON or bad Content-Length |
| 404 | `unknown-lesson` | no lesson with that id or slug |
| 409 | `manifest-rejected` | the pure record-time manifest read is rejected or missing |
| 409 | `identity-mismatch` | manifest `lesson_uid` differs from DB authority |
| 409 | `blocks-unavailable` | manifest/profile grants no interactive editor affordance |
| 409 | `unsafe-file` | a descriptor is not a single-link regular file, or a parent/root is unsafe |
| 409 | `file-conflict` | `base_rev` differs or the descriptor identity changes before publish |
| 413 | `payload-too-large` | request stream exceeds 512 KiB |
| 413 | `file-too-large` | stored or submitted raw file exceeds 64 KiB |
| 415 | `unsupported-media-type` | save is not `application/json` |
| 422 | `unknown-block` | block did not survive the record-time manifest read |
| 422 | `invalid-encoding` | stored bytes are not strict UTF-8 |
| 422 | `undiscoverable-path` | save target is outside/deeper than the artifact discovery contract |
| 429 | `rate-limited` | more than 30 save attempts in the lesson's 60-second window |

## Run routes

```
POST /learn/lessons/{lesson_id}/blocks/{block_id}/runs
POST /learn/lessons/by-slug/{slug}/blocks/{block_id}/runs
GET  /learn/runs/{job_id}
GET  /learn/runs/{job_id}/stream[?after=<seq>]
POST /learn/runs/{job_id}/cancel
```

The start aliases share one handler. The request is `application/json`, capped
at 16 KiB, and contains only the last saved revision and the logical-action
identity:

```json
{"file_rev":"sha256:…64 lowercase hex…","idempotency_key":"opaque 1–128 chars"}
```

The server re-reads the record-time manifest through the pure reader, derives
the path and runner from `block_id`, and opens the artifact under the F1
per-bundle lock. The one no-follow, single-link regular descriptor supplies
the size-capped strict-UTF-8 bytes and their hash. A revision mismatch refuses
with `file-conflict`; a missing file is `file-missing`. Those exact verified
bytes—not a later path read—are the immutable runner snapshot.

A start succeeds with the in-memory job identity and current state:

```json
{"ok":true,"job_id":"…","state":"STARTING","replayed":false}
```

`(lesson_uid, idempotency_key)` retains `(block_id, file_rev, job_id)` with the
job. An identical replay returns the same job with `replayed: true`, before
health, rate, capacity, manifest, or filesystem work. Reuse for another block
or revision is `idempotency-conflict`. A count-evicted job keeps a tombstone
until its 15-minute retention deadline and answers `job-missing`.

All admission decisions live in the runner service: at most one active job per
lesson and two globally. Starts permit 10 charged validations/admissions per
lesson per 60 seconds. Grammar/manifest/hash/file refusals and every admitted
job are charged; replay, idempotency conflict, rate-limit refusal, and a busy
preflight are uncharged. A capacity race or runner-health/pre-reservation
admission failure after validation refunds its slot.

Cold runner-health probes execute on a worker thread outside the service lock,
so they do not stall status, stream, or cancel handling on the ASGI event loop.
Admission rechecks replay, lifecycle, rate, and capacity after the probe before
reserving a job.

Status is `Cache-Control: no-store` and returns the job/block/runner/revision,
state, and `event_recorded`. A terminal status also returns `cause`, optional
`exit_code` or `signal`, `truncated`, and `duration_ms`.

### SSE output and reconnect

The stream response is `text/event-stream`. `after` is the last applied
sequence number; `Last-Event-ID` is accepted when `after` is absent. No cursor
means replay from sequence zero. Browser requests must be same-origin (or
`Sec-Fetch-Site: none`); origin-less non-browser loopback clients remain
accepted. This check runs before a reader slot is reserved. Every later output
record is:

```
id: 7
event: output
data: {"seq":7,"stream":"stdout","text":"invented output\n"}
```

Sequence numbers increase across stdout and stderr. Text chunks are at most
32 KiB (the implementation reads 8 KiB raw chunks), and all readers share the
job's one 1 MiB retained output ring by cursor—there are no per-reader queues.
At most two readers attach to a job. Idle streams emit a comment heartbeat
about every 15 seconds. Disconnecting releases the reader slot.

After process reap and both stream EOFs, exactly one terminal record is sent:

```
id: 8
event: exit
data: {"seq":8,"cause":"exit","exit_code":0,"truncated":false,"duration_ms":42}
```

Terminal causes are `exit`, `signal`, `timeout`, `cancelled`, `output-limit`,
`spawn-failed`, or `shutdown`. Cancel is idempotent for active and terminal
jobs; a winning cancel uses the runner's SIGKILL tree path and the stream still
ends with `cause: "cancelled"`. Once the child has already reaped, a late cancel
cannot replace its natural `exit` or `signal` cause.

Terminal jobs remain for 15 minutes, with at most eight retained. Oldest
terminal jobs are evicted first; active jobs and jobs with attached streams are
excluded. A protected job is pruned after its last reader detaches when it is
otherwise expired or over the count bound. After eviction, status, stream, and
cancel return `job-missing`. Its idempotency replay entry remains valid for as
long as an attached reader keeps the job retained, even past the ordinary TTL.

Each terminal job attempts one best-effort `lesson_run` event containing
`lesson_uid`, `lesson_id`, `slug`, `block_id`, `runner_id`, `file_rev`, `cause`,
optional `exit_code`, `truncated`, and `duration_ms`. It contains no stdout or
stderr. Terminal status waits for this attempt so `event_recorded` is an honest
boolean. Shutdown jobs use the same path.

## Consolidated refusal matrix

Port-level bridge errors use the same `error` codes once the frontend membrane
is activated; the iframe never receives raw HTTP status as authority.

| status | error | condition |
|--------|-------|-----------|
| 400 | `invalid-block-id`, `invalid-base-rev`, `invalid-content` | editor request grammar |
| 400 | `invalid-file-rev`, `invalid-idempotency-key`, `invalid-cursor` | run request/cursor grammar |
| 400 | `invalid-json`, `invalid-request` | malformed/non-object JSON or bad Content-Length |
| 403 | `forbidden` | browser SSE request is not same-origin |
| 404 | `unknown-lesson` | no lesson with that id or slug |
| 404 | `job-missing` | job is unknown, expired, or count-evicted |
| 409 | `manifest-rejected`, `identity-mismatch`, `blocks-unavailable` | manifest/profile refuses the affordance |
| 409 | `unsafe-file` | root, parent, or artifact descriptor is unsafe |
| 409 | `file-missing` | declared run artifact does not exist |
| 409 | `file-conflict` | save base or run revision differs/current identity changed |
| 409 | `runner-unavailable` | health, lifecycle, sandbox, or scope prerequisite is unavailable |
| 409 | `busy` | lesson/global job cap or two-reader cap is full |
| 409 | `idempotency-conflict` | key was used for another block/revision |
| 413 | `payload-too-large`, `file-too-large` | request or artifact exceeds its byte cap |
| 415 | `unsupported-media-type` | mutating request is not `application/json` |
| 422 | `unknown-block` | block did not survive the record-time manifest read |
| 422 | `unknown-runner`, `incompatible-runner` | runner is absent/unregistered or rejects the suffix |
| 422 | `invalid-encoding`, `undiscoverable-path` | artifact text/path is outside the contract |
| 429 | `rate-limited` | save or start window is exhausted; `Retry-After` is present |
