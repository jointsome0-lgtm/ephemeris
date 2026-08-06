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
  were skipped by the quarantine rule below — which npm's own resolver, under
  the `.npmrc` gate, would have skipped identically.

## Refresh policy

Manual and rare. When bumping or adding a library:

1. **Quarantine**: only take a release that is **at least 30 days old** — a
   cheap filter against a freshly compromised publish, not the control. Do not
   compare dates by hand; let the package manager refuse. The repository's
   `.npmrc` sets `min-release-age=30`; resolve the version in a scratch
   directory that carries that file, and npm both picks the newest release
   outside the window and errors on a pin inside it:

   ```
   scratch=$(mktemp -d) && cp .npmrc "$scratch"/ && cd "$scratch" && : > empty.npmrc

   # a built environment, so only this project's .npmrc speaks: no ~/.npmrc,
   # no global file, no npm_config_* variables inherited from the shell
   env -i HOME="$HOME" PATH="$PATH" ${https_proxy:+https_proxy="$https_proxy"} \
       npm_config_userconfig="$PWD/empty.npmrc" npm_config_globalconfig=/dev/null \
       bash -c '
         npm init -y >/dev/null
         # liveness check, then resolve — chained, so a dead gate resolves nothing
         npm config list | grep -q "^before = \"$(date -u -d "30 days ago" +%F)T" \
           && npm install --package-lock-only <name> \
           && npm ls --package-lock-only <name>'
   ```

   Two details that quietly turn this into a no-op if you skip them. The `cp`
   is load-bearing: npm reads a project `.npmrc` from the directory it runs
   in, so a bare `mktemp -d` leaves the gate behind in the repository and the
   resolver happily hands back a release published yesterday. And the `grep` is
   the gate's own liveness check — npm below 11.6 does not know the key, warns
   `Unknown project config "min-release-age"` and carries on unquarantined. npm
   implements the setting by translating it into a cutoff date, so a live gate
   is exactly a `before = "<today − 30 days>"` line in `npm config list`; no
   such line, no quarantine, and the answer is a newer npm rather than a
   version picked by eye.

   The check matches the *date*, not merely the key, because `before` has an
   independent life: a cutoff inherited from `~/.npmrc` or the environment
   would print the same line while admitting anything published before some
   unrelated date. (An npm that does understand `min-release-age` refuses the
   combination outright — `--min-release-age cannot be provided when using
   --before` — so that reading is aimed at the old-npm case, where the
   inherited value silently wins.) And the `&&` chain is what makes the check a
   gate rather than a remark: when it fails nothing is resolved, where three
   separate lines would have sailed on into an unquarantined install.

   The `env -i` exists because a personal npm config can weaken the gate in
   more ways than one date check can spot — `min-release-age-exclude` exempts
   named packages from the age rule outright, and every setting also travels as
   an `npm_config_*` variable, which npm matches case-insensitively, so
   clearing a list of names is a game you lose eventually. Building the
   environment instead of pruning it leaves exactly one source of
   configuration: the `.npmrc` just copied in. Verified against a hostile
   personal config (`min-release-age-exclude=katex`, `before=2099-01-01`, plus
   `Npm_Config_before` and an `npm_config_registry` override in the
   environment): inside it `npm config list` shows nothing but the generated
   cutoff, and `katex` resolves to 0.17.0 rather than the still-quarantined
   0.18.1.

   `npm ls` needs `--package-lock-only` too: `--package-lock-only` on the
   install writes the lockfile without populating `node_modules`, and a plain
   `npm ls` reports what is installed — an empty tree and a nonzero exit.

   ```
   npm error notarget No matching version found for mermaid@11.16.1
     with a date before 7/7/2026
   ```

   is what a too-fresh pin looks like. (`bun` has the same gate as
   `--minimum-release-age=<seconds>`.) Note that `npm ci` deliberately ignores
   this and replays the lockfile as committed.
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
