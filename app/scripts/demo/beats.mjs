// The demo video's nine beats: what is on screen and what the caption says, as data the recorder
// (record.mjs) plays and the encoder (encode.mjs) times. Spec: docs/superpowers/specs/
// 2026-10-29-demo-video-design.md §3. The page is one document whose views are
// `section[data-view="<track>-<slug>"]`, switched by the location hash.
//
// Action kinds: goto (set the hash), scrollTo (smooth-scroll a selector into view, then wait ms),
// click, clickUntilDisabled (step a control until it disables, waiting waitMs between clicks, at most
// max times), select (choose a value in a <select>), wait, resize, caption (show captions[index]),
// phoneFrame (show the page at phone size in a centred frame, scroll a selector in it, wait ms, remove it).

export const VIEWPORT = { width: 1920, height: 1080 };
export const PHONE = { width: 390, height: 844 };

// The closing caption's figure. Suite summaries at main 35ceb8c on 2026-10-29: app 2,484; Python 1,628
// (harness 33, llms 128, inspect 38, ml 208, mcp 285, agents 412, data 271, ontology 108, infra 145); plus
// the Go packages. The track count is the home page's own (`tracks.length`). Update the captions and this
// note together.
export const TEST_COUNT_NOTE = 'eight tracks; over 4,000 tests: app 2,484 + Python 1,628 + Go, counted 2026-10-29 at main 35ceb8c';

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
    actions: [goto('#home'), wait(1500), scrollTo(LAST_TRACK, 4000), caption(1), wait(4500)],
  },
  {
    id: 'anatomy',
    captions: ['Every lesson: concept, a diagram, the code, a try-it, a quiz.', 'Diagrams step through the mechanism.'],
    actions: [
      goto('#ontology-what'), wait(1500), scrollTo(`${WHAT} svg`, 2500),
      click(`${WHAT} button:text-is("Next")`), wait(2500),
      click(`${WHAT} button:text-is("Next")`), wait(2500),
      caption(1),
      click(`${WHAT} button:text-is("Next")`), wait(3500),
    ],
  },
  {
    id: 'replay',
    captions: ['Multi-agent runs are recorded once and replayed free, byte for byte.', 'Watch the supervisor hand off, research, draft, review.'],
    actions: [
      goto('#agents-multi-agent'), wait(1500), scrollTo(DESK_RUN, 2500),
      click(`${DESK_RUN} .mc-btn-primary`), wait(1500),
      click(`${DESK_RUN} .mc-btn-primary`), wait(1500),
      click(`${DESK_RUN} .mc-btn-primary`), wait(1500),
      caption(1),
      { kind: 'clickUntilDisabled', selector: `${DESK_RUN} .mc-btn-primary`, waitMs: 1500, max: 20 },
      wait(2000),
    ],
  },
  {
    id: 'code',
    captions: ['Python first. Go twins where it matters, equal on shared tests.'],
    actions: [
      goto('#infra-routing'), wait(1500), scrollTo(`${ROUTING} .codetabs`, 2500),
      click(`${ROUTING} .codetabs [role="tab"][data-lang="go"]`), wait(2500),
      scrollTo(`${ROUTING} .codetabs [role="tabpanel"][data-panel="go"]`, 4000), wait(2000),
    ],
  },
  {
    id: 'aid',
    captions: ['Interactive aids read the course’s own data.\nHere: a gateway trace, stage by stage.'],
    actions: [
      goto('#infra-capstone'), wait(1500), scrollTo(`${CAPSTONE} select`, 2500),
      { kind: 'select', selector: `${CAPSTONE} select >> nth=2`, value: 'cached' }, wait(1500),
      click(`${CAPSTONE} table tbody tr`), wait(3000),
      scrollTo(`${CAPSTONE} ol`, 3000), wait(3500),
    ],
  },
  {
    id: 'quiz',
    captions: ['Learning checks after every lesson, with the reasoning.'],
    actions: [
      goto('#ontology-what'), wait(1500), scrollTo(`${WHAT} .quiz`, 2500),
      ...QUIZ_PICKS.flatMap((o, q) => [click(option(q, o)), wait(900)]),
      scrollTo(`${WHAT} .quiz`, 1200),
      click(`${WHAT} .quiz .btn-primary`), wait(6000),
    ],
  },
  {
    id: 'gate',
    captions: ['Every number on every page is pinned by a test.\nGates are green or red on purpose.'],
    actions: [
      goto('#infra-capstone'), wait(1500), scrollTo(`${CAPSTONE} :text("The capstone gate")`, 2500), wait(4000),
      goto('#infra-budgets'), wait(1500), scrollTo(`${BUDGETS} .codetabs`, 3000), wait(2500),
    ],
  },
  {
    id: 'phone',
    captions: ['Works at phone width. No horizontal scroll.'],
    actions: [
      goto('#home'), wait(500),
      { kind: 'phoneFrame', hash: '#infra-capstone', selector: `${CAPSTONE} .mc-scroll`, ms: 9000, ...PHONE },
      wait(500),
    ],
  },
  {
    id: 'close',
    captions: ['Eight tracks. Over 4,000 tests. Zero invented numbers.'],
    actions: [goto('#home'), wait(1500), scrollTo(LAST_TRACK, 3000), wait(8000)],
  },
];
