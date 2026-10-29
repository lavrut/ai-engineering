// What the recorder draws over the page besides the caption: a visible cursor that glides to each
// target and presses, and a full-page overlay that fades views in and out. Injected only while
// recording; never part of the page.

export const CURSOR_MS = 600;     // the glide to a target
export const PRESS_MS = 220;      // the press animation
export const TRANSITION_MS = 350; // each half of a view change (fade out, fade in)

const ARROW = encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28"><path d="M6 3 L6 22 L11 17.5 L14.5 25 L18 23.5 L14.5 16 L21 16 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>',
);

export const CURSOR_CSS = `
#demo-cursor {
  position: fixed;
  left: 0;
  top: 0;
  width: 28px;
  height: 28px;
  background: url("data:image/svg+xml,${ARROW}") no-repeat center / contain;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.45));
  transform: translate(960px, 540px);
  transition: transform ${CURSOR_MS}ms cubic-bezier(0.2, 0.8, 0.2, 1), opacity 250ms ease;
  opacity: 0;
  z-index: 9998;
  pointer-events: none;
}
#demo-cursor.is-on { opacity: 1; }
#demo-cursor.is-press { animation: demo-press ${PRESS_MS}ms ease; }
#demo-ripple {
  position: fixed;
  left: 0;
  top: 0;
  width: 44px;
  height: 44px;
  margin: -22px 0 0 -22px;
  border: 3px solid rgba(45, 86, 214, 0.9);
  border-radius: 50%;
  opacity: 0;
  z-index: 9997;
  pointer-events: none;
}
#demo-ripple.is-on { animation: demo-ripple 450ms ease-out; }
@keyframes demo-press { 50% { transform: var(--demo-at) scale(0.82); } }
@keyframes demo-ripple { 0% { opacity: 0.9; transform: scale(0.4); } 100% { opacity: 0; transform: scale(1.4); } }
`;

export const OVERLAY_CSS = `
#demo-overlay {
  position: fixed;
  inset: 0;
  background: var(--bg, #ffffff);
  opacity: 0;
  transition: opacity ${TRANSITION_MS}ms ease;
  z-index: 9990;
  pointer-events: none;
}
#demo-overlay.is-on { opacity: 1; }
`;

export const OVERLAY_JS = `
(() => {
  const make = (id) => { let el = document.getElementById(id); if (!el) { el = document.createElement('div'); el.id = id; document.body.appendChild(el); } return el; };
  const cursor = make('demo-cursor'), ripple = make('demo-ripple'), overlay = make('demo-overlay');
  window.__demoCursorTo = (x, y) => {
    const at = 'translate(' + x + 'px, ' + y + 'px)';
    cursor.style.setProperty('--demo-at', at);
    cursor.style.transform = at;
    ripple.style.left = x + 'px'; ripple.style.top = y + 'px';
  };
  window.__demoCursorShow = (on) => cursor.classList.toggle('is-on', !!on);
  window.__demoCursorPress = () => {
    cursor.classList.remove('is-press'); ripple.classList.remove('is-on');
    void cursor.offsetWidth;
    cursor.classList.add('is-press'); ripple.classList.add('is-on');
  };
  window.__demoOverlay = (on) => overlay.classList.toggle('is-on', !!on);
  window.__demoScrollTo = (top, ms) => new Promise((done) => {
    const from = window.scrollY, delta = top - from, t0 = performance.now();
    const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
    const step = (now) => { const p = Math.min(1, (now - t0) / ms); window.scrollTo(0, from + delta * ease(p)); if (p < 1) requestAnimationFrame(step); else done(); };
    requestAnimationFrame(step);
  });
})();
`;

/** Add the cursor, the ripple and the overlay to the page once. */
export async function installOverlay(page) {
  await page.addStyleTag({ content: CURSOR_CSS + OVERLAY_CSS });
  await page.evaluate(OVERLAY_JS);
}

/** Glide the cursor to the centre of a locator's box (the cursor's tip is its top-left corner). */
export async function cursorTo(page, locator, wait) {
  const box = await locator.boundingBox();
  if (!box) return;
  const x = Math.round(box.x + box.width / 2);
  const y = Math.round(box.y + box.height / 2);
  await page.evaluate(({ x, y }) => window.__demoCursorTo(x - 4, y - 3), { x, y });
  await wait(CURSOR_MS + 120);
}

export async function cursorShow(page, on) {
  await page.evaluate((v) => window.__demoCursorShow(v), on);
}

export async function cursorPress(page, wait) {
  await page.evaluate(() => window.__demoCursorPress());
  await wait(PRESS_MS);
}

/** Fade the overlay in (true) or out (false) and wait for it. */
export async function overlay(page, on, wait) {
  await page.evaluate((v) => window.__demoOverlay(v), on);
  await wait(TRANSITION_MS + 40);
}

/** Scroll the window so `locator` sits at `offset` px from the top, eased over `ms`. */
export async function scrollToLocator(page, locator, ms, offset = 24) {
  const top = await locator.evaluate((node) => node.getBoundingClientRect().top + window.scrollY);
  await page.evaluate(({ top, ms }) => window.__demoScrollTo(Math.max(0, top), ms), { top: top - offset, ms });
}
