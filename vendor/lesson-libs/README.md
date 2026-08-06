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
bundle the same way: the flattened path stays, the bytes change.

## Inventory

Every artifact was taken from the official npm tarball, whose `sha512`
integrity was checked against the registry's `dist.integrity` before
extraction; per-file sha256 is in `SHASUMS256`.

| library | version | published | retrieved | files | tarball |
|---------|---------|-----------|-----------|-------|---------|
| [d3](https://d3js.org) | 7.9.0 | 2024-03-12 | 2026-08-06 | `d3.min.js` (UMD) | `https://registry.npmjs.org/d3/-/d3-7.9.0.tgz` |
| [KaTeX](https://katex.org) | 0.17.0 | 2026-05-22 | 2026-08-06 | `katex.min.js` (UMD), `katex.min.css`, `fonts/*.woff2` | `https://registry.npmjs.org/katex/-/katex-0.17.0.tgz` |
| [mermaid](https://mermaid.js.org) | 11.16.0 | 2026-06-25 | 2026-08-06 | `mermaid.min.js` (IIFE, global `mermaid`) | `https://registry.npmjs.org/mermaid/-/mermaid-11.16.0.tgz` |

Notes from the eyeball pass at retrieval time:

- All three bundles are self-contained: no `sourceMappingURL`, no dynamic
  `import()` of sibling chunks, no runtime remote URLs (the `http://…` strings
  are XML namespaces and documentation links in error messages).
- KaTeX ships only `.woff2` here. Its `@font-face` rules list woff2 first, so a
  current browser never asks for the `.woff`/`.ttf` fallbacks; skipping them
  saves ~1.4 MB. `katex.min.css` refers to `fonts/…` relatively, which is why
  the font directory is kept next to the CSS.
- Newer releases existed at retrieval time (KaTeX 0.18.1, mermaid 11.16.1) and
  were skipped by the quarantine rule below.

## Refresh policy

Manual and rare. When bumping or adding a library:

1. **Quarantine**: only take a release that is **at least 30 days old**. That
   is a cheap filter against a freshly compromised publish; it is not the
   control.
2. **Pin** the exact version — never a range, never `latest`.
3. **Fetch** the official npm tarball and check its `sha512` against
   `dist.integrity` from `https://registry.npmjs.org/<name>/<version>` before
   extracting anything.
4. **Eyeball** once: the bundle is the published minified artifact, has no
   source-map or chunk references to files we do not ship, and makes no
   runtime network calls.
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
