# The site

Moved from the repository README on 2026-10-29.

## The site

```sh
cd app
npm install
npm run dev        # dev server at http://localhost:4321
npm test           # vitest
npm run build      # astro check + test type-check + static build into dist/
npm run package    # dist/ -> publish/ in the page-host format
```

`npm run package` writes `publish/index.html` without a document skeleton (the host supplies one), makes every `_astro/` reference relative, and exits 1 listing any external URL outside the allowed hosts (`fonts.googleapis.com`, `fonts.gstatic.com`, `cdnjs.cloudflare.com`, `cdn.jsdelivr.net`, `unpkg.com`).

## Adding content

Every track and lesson follows [docs/STYLE.md](docs/STYLE.md): the lesson anatomy, writing rules, components, design tokens and code conventions. Start new work from a template:

```sh
cd app
npm run new:lesson -- <track> <slug> "<Title>"   # from templates/lesson.mdx
cp templates/track.yaml src/content/tracks/<id>.yaml
```

`npm test` enforces the checkable rules: section order, no leftover `TODO`, glossary terms, colors only from `src/styles/tokens.css`, and valid UTF-8 with no garbled characters in any source file.

## The demo video

`cd app && npm run demo` records the packaged site (`npm run package` first) through nine captioned beats with Playwright and encodes a silent, 1080p, H.264 MP4 of about two and a half minutes at `app/demo/ai-engineering-demo.mp4`, with an `.srt` beside it and one still per beat under `app/demo/stills/` (all git-ignored). `npm run demo -- --dry` walks the beats without recording and fails on the first missing element; `--theme dark` records the dark theme. `node scripts/demo/media.mjs` then writes the README's poster frame and GIF teaser to `docs/media/`. The beats, their captions and their selectors are data in `app/scripts/demo/beats.mjs`; the design is in `docs/superpowers/specs/2026-10-29-demo-video-design.md`. ffmpeg must be on the PATH; the pinned `playwright` package uses the Chromium build already in Playwright's cache.
