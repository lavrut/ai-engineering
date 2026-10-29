# AI Engineering: captioned demo video (design)

Date: 2026-10-29
Status: design approved in conversation. This written spec is awaiting review.
Builds on: the published course page (`app/publish`, v16 of the artifact), the app's components and their data, and the machine's tooling (ffmpeg 8, Playwright's cached Chromium builds, Node 26).

## 1. Intent

**Outcome.** A silent, captioned demo video of the course page for a LinkedIn post: about two and a half minutes, 1920×1080, H.264 MP4, with an `.srt` sidecar carrying the same captions. It shows the breadth of the course in nine beats: the home page, a lesson's anatomy with a stepping diagram, a replayed multi-agent run, code in Python and Go, an interactive aid driven live, a learning check, a capstone gate, the phone-width layout, and a closing card.

**Audience.** LinkedIn viewers scrolling a feed: autoplay is muted, so the captions carry the story; the first seconds must say what the thing is.

**Success criteria.**
- `npm run demo` produces `app/demo/ai-engineering-demo.mp4` and `app/demo/ai-engineering-demo.srt` from the packaged page without manual steps; rerunning after a course change regenerates both.
- The MP4 is 1920×1080, H.264 yuv420p, 30 fps, between 140 and 170 seconds, under 200 MB; the `.srt` has one cue per caption, in order, non-overlapping, timed from the recording's own clock.
- A dry run proves every element the beats need exists in the current page.
- Nine stills, one per beat, show the right thing on screen with the caption legible and nothing clipped.

**Stated by the user:** for a LinkedIn post; captions only, no sound; the breadth tour of nine beats; no call to action; produce it with a Playwright recording script and captions drawn in the page.

**Assumptions (accepted):**
- One new dev dependency, `playwright`, pinned to the version whose Chromium build (1248) is already in Playwright's cache, so the install fetches the npm package only.
- The video files are git-ignored; the script and its data are committed.
- No lesson, component or data file is changed for the video.

## 2. Scope

**In scope:** `app/scripts/demo/` (record, beats, caption, encode), the `demo` npm script, the dependency, the `.gitignore` entry, the output files, the nine stills.

**Out of scope:** narration, music, title animations beyond caption fades, a vertical or square cut, publishing anywhere (the user uploads the file), any change to the course itself.

## 3. The beats and the captions

Light theme (emulated `prefers-color-scheme: light` and `data-theme="light"`; `--theme dark` records the dark theme instead), 1920×1080, the page served from `app/publish` on the loopback. Each caption is at most two short lines and stays for its beat; two captions in one beat change halfway. A drawn cursor glides to every element before it is clicked and shows a press; every view change fades out and in over 350 ms each way; scrolls are eased over 900 ms. (Changed on 2026-10-29 at the user's request from the dark, cursor-less, hard-cut first version.)

| # | Seconds | On screen | Caption |
|---|---|---|---|
| 1 | 0–10 | Home page; slow scroll over the eight track cards | "AI Engineering: a hands-on course with real, runnable code." then "Eight tracks, from the harness to the gateway." |
| 2 | 10–30 | Ontology lesson 1; the layers diagram stepping Next three times | "Every lesson: concept, a diagram, the code, a try-it, a quiz." then "Diagrams step through the mechanism." |
| 3 | 30–55 | An Agents lesson with the trace replay; step through a recorded supervisor run: handoffs, tool calls, the human's decline | "Multi-agent runs are recorded once and replayed free, byte for byte." then "Watch the supervisor hand off, research, draft, review." |
| 4 | 55–70 | A CodeTabs block; click the Go tab; scroll the section | "Python first. Go twins where it matters, equal on shared tests." |
| 5 | 70–90 | AI Infrastructure capstone; TraceExplorer: filter by outcome, open a trace, its stage decisions | "Interactive aids read the course's own data. Here: a gateway trace, stage by stage." |
| 6 | 90–105 | A quiz: pick an answer, the explanation appears | "Learning checks after every lesson, with the reasoning." |
| 7 | 105–120 | The capstone gate: four green lines; then a test file scrolling briefly | "Every number on every page is pinned by a test. Gates are green or red on purpose." |
| 8 | 120–135 | The page at phone width in a centred phone-sized frame; the same aid scrolls inside its box | "Works at phone width. No horizontal scroll." |
| 9 | 135–150 | Home page again, the track list; final card | "Eight tracks. Over 4,000 tests. Zero invented numbers." |

(The course has eight tracks, as its home page counts them; an earlier draft of this table said nine.)

The closing figure's source: the suite summaries at main 35ceb8c (app 2,484; Python 1,628 across harness 33, llms 128, inspect 38, ml 208, mcp 285, agents 412, data 271, ontology 108, infra 145; plus the Go packages). The caption says "over 4,000"; the beats file records the figure and its date in a comment so a later run can update it.

## 4. The pipeline

```
app/scripts/demo/
├── record.mjs      serves app/publish on a free port; launches Chromium at 1920×1080 with recordVideo;
│                   runs the beats; writes app/demo/raw.webm and app/demo/timings.json
├── beats.mjs       the nine beats as data: {captions: [...], actions: [...]} with actions
│                   goto(hash) | scrollTo(selector, ms) | click(selector) | wait(ms) | resize(w, h) | type(selector, text)
├── caption.mjs     page-side: injects a fixed caption strip in the bottom third using the site's font and
│                   colour tokens, fades captions in and out, and exposes showCaption(text)
└── encode.mjs      ffmpeg: WebM → H.264 MP4 (30 fps, yuv420p, fade in 0.5 s and out 1 s, faststart);
                    writes the .srt from timings.json; prints ffprobe's duration, size, fps, codec
```

- `npm run demo` = record then encode; `npm run demo -- --dry` runs every beat without recording and fails naming the first missing selector.
- The caption timings are recorded when each caption is shown (the page's clock relative to the recording start), so the `.srt` matches the video even when a beat runs long.
- Captions are drawn in the page, not by ffmpeg, so they use the site's typography and need no font configuration.
- The server is a small static file server inside `record.mjs` (Node's `http`), loopback only, on a free port.
- Output under `app/demo/` (git-ignored): `raw.webm`, `timings.json`, `ai-engineering-demo.mp4`, `ai-engineering-demo.srt`, and `stills/beat-N.png` (one frame from the middle of each beat, extracted by ffmpeg).

## 5. Error handling

- A beat whose selector is missing fails the run (and the dry run) naming the beat and the selector; nothing is encoded.
- If `app/publish` is missing, `record.mjs` says to run `npm run package` first and exits 2.
- If `ffmpeg` or `ffprobe` is not on the PATH, `encode.mjs` says so and exits 2; the WebM stays for a manual encode.
- The duration, size, frame-size and codec checks in `encode.mjs` fail the run with the measured values when outside the bounds in §1.
- The `.srt` writer refuses overlapping cues (a bug in the timings) rather than writing a broken file.

## 6. Testing and verification

- `npm run demo -- --dry` is the selector test; it runs in seconds.
- A Vitest unit test for the pure parts: the `.srt` formatter (timing format, ordering, overlap refusal) and the beats' shape (nine beats, every caption under 90 characters and two lines, every action of a known kind).
- The recording itself is verified by `encode.mjs`'s ffprobe checks and by looking at the nine stills.
- No paid calls, no network beyond the one `npm install` of the dev dependency.

## 7. Hand-off

`app/demo/ai-engineering-demo.mp4` to upload, `app/demo/ai-engineering-demo.srt` as the optional caption file, and the nine stills copied to the session's scratchpad for a glance before uploading. The script and its data land on a branch `feat/demo-video`, merged with the user's go-ahead.

## 8. Open items

- Which Agents lesson carries the trace replay aid and how its stepping control is exposed (the plan reads `app/src/components/agents/AgentTrace.tsx` and picks the lesson and selectors).
- The exact `playwright` version matching Chromium build 1248 (the plan checks Playwright's browsers manifest for the installed build and pins that version).
