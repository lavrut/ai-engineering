// The demo video's nine beats: what is on screen and what the caption says, as data the recorder
// (record.mjs) plays and the encoder (encode.mjs) times. Spec: docs/superpowers/specs/
// 2026-10-29-demo-video-design.md §3. The page is one document whose views are
// `section[data-view="<track>-<slug>"]`, switched by the location hash.
//
// Action kinds: goto (fade out, set the hash, fade in), scrollTo (ease a selector into view, then hold
// ms), click, clickUntilDisabled (step a control until it disables, waiting waitMs between clicks, at
// most max times), select (choose a value in a <select>), wait, resize, caption (show captions[index]),
// phoneFrame (show the page at phone size in a centred frame, scroll a selector in it, hold ms, remove it).
// Every click and select is preceded by the cursor's glide (about 0.7 s) and a press (0.2 s); every
// goto costs about 0.9 s of fading; every scrollTo eases over 0.9 s before its hold. Those come on top
// of the fixed waits plannedMs() sums.

export const VIEWPORT = { width: 1920, height: 1080 };
export const PHONE = { width: 390, height: 844 };

// The closing caption's figure. Suite summaries at main 35ceb8c on 2026-10-29: app 2,484; Python 1,628
// (harness 33, llms 128, inspect 38, ml 208, mcp 285, agents 412, data 271, ontology 108, infra 145); plus
// the Go packages. The track count is the home page's own (`tracks.length`). Update the captions and this
// note together.
export const TEST_COUNT_NOTE = 'eight tracks; over 4,000 tests: app 2,484 + Python 1,628 + Go, counted 2026-10-29 at main 35ceb8c';

/** The fixed waiting the beats plan, in ms (waits, scroll holds and the phone frame's hold); the cursor,
 *  the transitions, the replay beat's stepping and the page's own load times come on top. */
export function plannedMs(beats) {
  return beats.flatMap((b) => b.actions).reduce((sum, a) => sum + (a.kind === 'wait' || a.kind === 'scrollTo' || a.kind === 'phoneFrame' ? a.ms : 0), 0);
}

const view = (id) => `section[data-view="${id}"]`;
const goto = (hash) => ({ kind: 'goto', hash });
const scrollTo = (selector, ms) => ({ kind: 'scrollTo', selector, ms });
const click = (selector) => ({ kind: 'click', selector });
const wait = (ms) => ({ kind: 'wait', ms });
const caption = (index) => ({ kind: 'caption', index });

const HOME = view('home');
const WHAT = view('ontology-what');
const DESK_RUN = `${view('agents-multi-agent')} section[aria-label="Desk run step by step"]`;
const ROUTING = view('infra-routing');
const CAPSTONE = view('infra-capstone');
const BUDGETS = view('infra-budgets');
const LAST_TRACK = `${HOME} ol.catalog li:last-child`;
// Ontology lesson 1's quiz: the right option of each question (the lesson's `answer:` indexes 1, 3, 0).
const QUIZ_PICKS = [1, 3, 0];
const option = (q, o) => `${WHAT} .quiz .quiz-options >> nth=${q} >> label.quiz-option >> nth=${o}`;

export const BEATS = [
  {
    id: 'home',
    captions: ['AI Engineering: a hands-on course with real, runnable code.', 'Eight tracks, from the harness to the gateway.'],
    actions: [goto('#home'), wait(800), scrollTo(LAST_TRACK, 3000), caption(1), wait(6000)],
  },
  {
    id: 'anatomy',
    captions: ['Every lesson: concept, a diagram, the code, a try-it, a quiz.', 'Diagrams step through the mechanism.'],
    actions: [
      goto('#ontology-what'), wait(800), scrollTo(`${WHAT} svg`, 1500),
      click(`${WHAT} button:text-is("Next")`), wait(2000),
      click(`${WHAT} button:text-is("Next")`), wait(2000),
      caption(1),
      click(`${WHAT} button:text-is("Next")`), wait(2500),
    ],
  },
  {
    id: 'replay',
    captions: ['Multi-agent runs are recorded once and replayed free, byte for byte.', 'Watch the supervisor hand off, research, draft, review.'],
    actions: [
      goto('#agents-multi-agent'), wait(800), scrollTo(DESK_RUN, 1500),
      click(`${DESK_RUN} .mc-btn-primary`), wait(1200),
      click(`${DESK_RUN} .mc-btn-primary`), wait(1200),
      click(`${DESK_RUN} .mc-btn-primary`), wait(1200),
      caption(1),
      { kind: 'clickUntilDisabled', selector: `${DESK_RUN} .mc-btn-primary`, waitMs: 1200, max: 20 },
      wait(1500),
    ],
  },
  {
    id: 'code',
    captions: ['Python first. Go twins where it matters, equal on shared tests.'],
    actions: [
      goto('#infra-routing'), wait(800), scrollTo(`${ROUTING} .codetabs`, 1500),
      click(`${ROUTING} .codetabs [role="tab"][data-lang="go"]`), wait(2000),
      scrollTo(`${ROUTING} .codetabs [role="tabpanel"][data-panel="go"]`, 3000), wait(1500),
    ],
  },
  {
    id: 'aid',
    captions: ['Interactive aids read the course’s own data.\nHere: a gateway trace, stage by stage.'],
    actions: [
      goto('#infra-capstone'), wait(800), scrollTo(`${CAPSTONE} select`, 1500),
      { kind: 'select', selector: `${CAPSTONE} select >> nth=2`, value: 'cached' }, wait(1000),
      click(`${CAPSTONE} table tbody tr`), wait(2000),
      scrollTo(`${CAPSTONE} ol`, 2000), wait(3000),
    ],
  },
  {
    id: 'quiz',
    captions: ['Learning checks after every lesson, with the reasoning.'],
    actions: [
      goto('#ontology-what'), wait(800), scrollTo(`${WHAT} .quiz`, 1500),
      ...QUIZ_PICKS.flatMap((o, q) => [click(option(q, o)), wait(600)]),
      scrollTo(`${WHAT} .quiz`, 800),
      click(`${WHAT} .quiz .btn-primary`), wait(5000),
    ],
  },
  {
    id: 'gate',
    captions: ['Every number on every page is pinned by a test.\nGates are green or red on purpose.'],
    actions: [
      goto('#infra-capstone'), wait(800), scrollTo(`${CAPSTONE} :text("The capstone gate")`, 1500), wait(3000),
      goto('#infra-budgets'), wait(800), scrollTo(`${BUDGETS} .codetabs`, 2000), wait(1500),
    ],
  },
  {
    id: 'phone',
    captions: ['Works at phone width. No horizontal scroll.'],
    actions: [
      goto('#home'), wait(300),
      { kind: 'phoneFrame', hash: '#infra-capstone', selector: `${CAPSTONE} .mc-scroll`, ms: 9500, ...PHONE },
      wait(300),
    ],
  },
  {
    id: 'close',
    captions: ['Eight tracks. Over 4,000 tests. Zero invented numbers.'],
    actions: [goto('#home'), wait(800), scrollTo(LAST_TRACK, 2000), wait(9000)],
  },
];
