# Lesson-libs shelf

Pinned, offline copies of the few libraries a lesson page may need when a
visualization outgrows hand-rolled SVG/CSS/JS (issue #146).

Lesson pages must be self-contained and work offline — the lesson CSP
(`app/routers/learn.py`, `interactive-local-v1`) allows `'self'` only, and
loading anything from a CDN is forbidden by the study-agent brief. This shelf
is the sanctioned local path: the app copies it into every lesson bundle, so
the agent references `assets/libs/…` by relative path and never downloads
anything.

This directory is **not** web-served. `app/static/vendor/` is the shelf for the
app's own front end (xterm); this one is for lesson bundles.

## Layout

```
vendor/lesson-libs/
  SHASUMS256          one `<sha256>  <relpath>` line per file, sha256sum format
  README.md           this file
  <name>/<version>/…  the pinned artifacts, byte-for-byte as published
```

`SHASUMS256` is the shelf's inventory as well as its checksum file: the seeder
copies exactly the files it lists, and `tests/test_200_lesson_libs.py` fails if
a listed file is missing or its bytes changed. Verify a checkout by hand with:

```
cd vendor/lesson-libs && sha256sum -c SHASUMS256
```

## Delivery into bundles

`app/services/lessons.py` → `seed_lesson_libs()`, called from
`prepare_terminal_workspace()` on every lesson-terminal open. The version
directory is flattened away, so pages reference stable paths:

| shelf                            | bundle                        |
|----------------------------------|-------------------------------|
| `d3/7.9.0/d3.min.js`             | `assets/libs/d3/d3.min.js`    |
| `katex/0.17.0/katex.min.css`     | `assets/libs/katex/katex.min.css` |
| `mermaid/11.16.0/mermaid.min.js` | `assets/libs/mermaid/mermaid.min.js` |

Real copies, never hardlinks: a shared inode would let one lesson's agent
rewrite the shelf for every other lesson. Seeding is idempotent and
self-healing — a file whose sha256 already matches is left alone, a missing or
modified one is rewritten on the next terminal open. A bumped version reaches a
bundle the same way: the flattened path stays, the bytes change; dropping a
library from the shelf, on the other hand, leaves the copies already in bundles
where they are, so remove those by hand if a page must stop working.

The seeded area carries its own `assets/libs/SHASUMS256`, which is both a
checkable inventory (`cd assets/libs && sha256sum -c SHASUMS256`) and the mark
that the app owns these names: a bundle without it predates the shelf, so
whatever already sits at a shelf path is moved aside (`.collision-<hex>`)
rather than overwritten.

## Inventory

Every artifact was taken from the official npm tarball, whose `sha512`
integrity was checked against the registry's `dist.integrity` before
extraction; per-file sha256 is in `SHASUMS256`.

| library | version | published | retrieved | files | tarball |
|---------|---------|-----------|-----------|-------|---------|
| [d3](https://d3js.org) | 7.9.0 | 2024-03-12 | 2026-08-06 | `d3.min.js` (UMD) | `https://registry.npmjs.org/d3/-/d3-7.9.0.tgz` |
| [KaTeX](https://katex.org) | 0.17.0 | 2026-05-22 | 2026-08-06 | `katex.min.js` (UMD), `katex.min.css` (derived, see below) | `https://registry.npmjs.org/katex/-/katex-0.17.0.tgz` |
| [mermaid](https://mermaid.js.org) | 11.16.0 | 2026-06-25 | 2026-08-06 | `mermaid.min.js` (IIFE, global `mermaid`) | `https://registry.npmjs.org/mermaid/-/mermaid-11.16.0.tgz` |

Notes from the eyeball pass at retrieval time:

- All three bundles are self-contained: no `sourceMappingURL`, no dynamic
  `import()` of sibling chunks, no runtime remote URLs (the `http://…` strings
  are XML namespaces and documentation links in error messages).
- **KaTeX's CSS is the one derived artifact here.** A lesson page is served
  with `sandbox allow-scripts`, so its document sits on an opaque origin, and
  a font fetch is a CORS request: upstream's `url(fonts/KaTeX_…)` is blocked
  from a null origin (verified in a browser against the real serving route —
  KaTeX rendered in fallback glyphs). `font-src 'self' data:` allows data URLs
  and those are not CORS-gated, so `scripts/build_lesson_libs_katex_css.py`
  inlines the 20 woff2 faces into the stylesheet and drops the `.woff`/`.ttf`
  fallbacks no lesson browser reads. Upstream `dist/katex.min.css` for 0.17.0
  is sha256
  `a34ad8fc188e8f5a3af7ceaa2a58d7210c6c9171335a15bff2b48ebcd6a6f5b0`; re-run
  the script on the tarball above to reproduce the file byte-for-byte. Nothing
  else on the shelf is modified.
- d3 is shipped whole, but the page CSP has no `'unsafe-eval'`, so the d3 APIs
  that compile a string — `d3.csvParse`, `d3.tsvParse`, `dsvFormat().parse` —
  throw `EvalError` on a lesson page. `d3.csvParseRows` and the rest of d3
  (selections, scales, axes, shapes, layouts) work; the study-agent brief says
  so. Patching the bundle to remove `new Function` was rejected: it would fork
  a minified upstream artifact for an API that pages, having no network, have
  little reason to call.
- Newer releases existed at retrieval time (KaTeX 0.18.1, mermaid 11.16.1) and
  were skipped by the quarantine rule below — which the resolver, under the
  `bunfig.toml` gate, would have skipped identically.

## Refresh policy

Manual and rare. When bumping or adding a library:

1. **Quarantine**: only take a release that is **at least 30 days old** — a
   cheap filter against a freshly compromised publish, not the control. Do not
   compare dates by hand; let the package manager refuse. The repository's
   `bunfig.toml` sets `minimumReleaseAge = 2592000` (seconds, not days), and
   under it bun both picks the newest release outside the window and errors on
   a pin inside it:

   ```
   scratch=$(mktemp -d) && cp bunfig.toml "$scratch"/ && cd "$scratch"
   echo '{"name":"scratch","private":true}' > package.json

   # 1. liveness: pin the newest published version. A live gate REFUSES it,
   #    naming itself. If it installs, read its publish date below before
   #    trusting anything — the gate may simply be dead.
   bun add "<name>@$(bun info <name> version)"

   # 2. resolve without a pin: the newest release outside the window
   bun add <name> && bun pm ls
   ```

   ```
   error: No version matching "katex" found for specifier "0.18.2"
     (blocked by minimum-release-age: 2592000 seconds)
   ```

   is what a live gate refusing a too-fresh pin looks like. If step 1 succeeds
   instead, `bun info <name> time` prints every version's publish date — check
   the one you just installed against today before concluding the gate worked;
   a newest release that is already older than 30 days proves nothing either
   way.

   The `cp` is load-bearing: bun reads a project `bunfig.toml` from the
   directory it runs in, so a bare `mktemp -d` leaves the gate behind in the
   repository and the resolver happily hands back a release published
   yesterday. A misspelled key is ignored in silence (checked:
   `minimumReleaseAgeTYPO` installs unquarantined without a word), which is why
   the liveness step is a behavioural check rather than a look at the file. A
   *wrongly typed* value is the one mistake bun refuses outright.

   Two ways the gate can be weakened that the check above will catch, since it
   exercises the real resolver: `minimumReleaseAgeExcludes` exempts named
   packages from the age rule, and a `~/.bunfig.toml` merges in underneath the
   project file. Neither exists on this machine today.

   Note that `bun install --frozen-lockfile` deliberately ignores all of this
   and replays the lockfile as committed — which is what CI runs.
2. **Pin** the exact version — never a range, never `latest`.
3. **Fetch** the official npm tarball and check its `sha512` against
   `dist.integrity` from `https://registry.npmjs.org/<name>/<version>` before
   extracting anything.
4. **Eyeball** once: the bundle is the published minified artifact, has no
   source-map or chunk references to files we do not ship, and makes no
   runtime network calls. A library that ships web fonts needs them inlined —
   see the KaTeX note above and `scripts/build_lesson_libs_katex_css.py`.
5. **Record**: add the version directory, update the inventory table above with
   source URL, publish date and retrieval date, and regenerate the checksums —
   the pin plus the hash is the actual control:

   ```
   cd vendor/lesson-libs
   find . -type f ! -name SHASUMS256 ! -name README.md -printf '%P\n' \
     | LC_ALL=C sort | xargs sha256sum > SHASUMS256
   ```

6. Drop the superseded version directory in the same change, and mention the
   bump in the study-agent brief only if the flattened path changes — the
   brief names libraries, not versions.
