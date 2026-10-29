// The caption strip the recorder draws in the page: a fixed band in the bottom third, the site's
// font and colour tokens (with plain fallbacks), one or two lines, a short fade between captions.
// It is injected only while recording and never ships in the page.

export const CAPTION_ID = 'demo-caption';

export const CAPTION_CSS = `
#${CAPTION_ID} {
  position: fixed;
  left: 50%;
  bottom: 9%;
  transform: translateX(-50%);
  max-width: 72%;
  padding: 0.6em 1.1em;
  border-radius: 12px;
  font: 600 40px/1.25 var(--font-display, system-ui, sans-serif);
  color: var(--ink, #ffffff);
  background: color-mix(in srgb, var(--surface, #000000) 86%, transparent);
  border: 1px solid var(--line, transparent);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
  opacity: 0;
  transition: opacity 250ms ease;
  z-index: 9999;
  white-space: pre-line;
  text-align: center;
  pointer-events: none;
}
#${CAPTION_ID}.is-on { opacity: 1; }
`;

export const CAPTION_JS = `
(() => {
  const id = ${JSON.stringify(CAPTION_ID)};
  let el = document.getElementById(id);
  if (!el) { el = document.createElement('div'); el.id = id; document.body.appendChild(el); }
  window.__demoCaption = (text) => {
    if (!text) { el.classList.remove('is-on'); return; }
    el.textContent = text;
    el.classList.add('is-on');
  };
})();
`;

/** Add the strip's style and script to the page once. */
export async function installCaption(page) {
  await page.addStyleTag({ content: CAPTION_CSS });
  await page.evaluate(CAPTION_JS);
}

/** Show a caption (an empty string hides it). */
export async function showCaption(page, text) {
  await page.evaluate((t) => window.__demoCaption(t), text);
}
