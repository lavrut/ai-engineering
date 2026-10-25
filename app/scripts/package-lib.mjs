// Pure transforms that turn Astro's static build into the page-host format:
// the host wraps index.html in its own document skeleton and serves the
// supporting files beside it, so every asset reference must be relative.

/** Hosts the published page may load scripts, styles and fonts from. */
export const ALLOWED_HOSTS = new Set([
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'cdnjs.cloudflare.com',
  'cdn.jsdelivr.net',
  'unpkg.com',
]);

/**
 * Rewrite root-absolute `/assets/` references to page-relative `./assets/`.
 * The leading "./" is required: Astro hydrates islands with
 * `import(componentUrl)`, and import() rejects a bare `assets/x.js`.
 */
function relativizeAssets(text) {
  return text.replace(/(?<![\w./-])\/assets\//g, './assets/');
}

/**
 * Head contents (minus charset/viewport metas, `<title>` first) followed by
 * body contents (and anything after </body>), with no doctype/html/head/body
 * tags and `/assets/` made `./assets/`.
 * @param {string} html
 * @returns {string}
 */
export function transformHtml(html) {
  const headMatch = /<head\b[^>]*>([\s\S]*?)<\/head>/i.exec(html);
  let head = headMatch ? headMatch[1] : '';
  const bodyOpen = /<body\b[^>]*>/i.exec(html);
  // Everything after <body>, including scripts Astro emits after </html>
  // (a page-level <script> outside the layout lands there).
  const body = bodyOpen
    ? html.slice(bodyOpen.index + bodyOpen[0].length).replace(/<\/(?:body|html)\s*>/gi, '')
    : '';

  head = head
    .replace(/<meta\s+charset\b[^>]*>\s*/gi, '')
    .replace(/<meta\s+name=["']?viewport["']?[^>]*>\s*/gi, '');

  let title = '';
  head = head.replace(/<title\b[^>]*>[\s\S]*?<\/title>\s*/i, (m) => {
    title = m.trim();
    return '';
  });

  const out = [title, head.trim(), body.trim()].filter(Boolean).join('\n');
  return relativizeAssets(out);
}

/**
 * Rewrite quoted `/assets/` references inside a chunk that lives in `assets/`.
 * @param {string} js
 * @returns {string}
 */
export function rewriteChunk(js) {
  return js.replace(/(["'`])\/assets\//g, '$1./');
}

/**
 * Absolute or protocol-relative URLs in loading positions (src=, href= on
 * non-anchor tags, url(), import()/import/from) whose host is not
 * allowlisted. Unique, in order.
 * @param {string} text
 * @returns {string[]}
 */
export function findBlockedUrls(text) {
  // Absolute (http/https) or protocol-relative (//host/...) URLs.
  const URL_RE = String.raw`((?:https?:)?\/\/[^"'\`\s)>]+)`;
  const found = [];

  // A tag runs to the first > outside a quoted attribute value.
  const tagRe = /<([a-zA-Z][\w-]*)\b(?:[^>"']|"[^"]*"|'[^']*')*>/g;
  const attrRe = new RegExp(String.raw`\b(?:src|href)\s*=\s*["']?` + URL_RE, 'gi');
  for (const tag of text.matchAll(tagRe)) {
    if (tag[1].toLowerCase() === 'a') continue;
    for (const m of tag[0].matchAll(attrRe)) found.push({ at: tag.index + m.index, url: m[1] });
  }

  const codeRes = [
    new RegExp(String.raw`url\(\s*["']?` + URL_RE, 'g'),
    new RegExp(String.raw`\bimport\s*\(\s*["'\`]` + URL_RE, 'g'),
    new RegExp(String.raw`\b(?:from|import)\s*["'\`]` + URL_RE, 'g'),
  ];
  for (const re of codeRes) {
    for (const m of text.matchAll(re)) found.push({ at: m.index, url: m[1] });
  }

  const blocked = [];
  for (const { url } of found.sort((a, b) => a.at - b.at)) {
    let host;
    try {
      host = new URL(url, 'https://base.invalid').hostname; // resolves //host too
    } catch {
      host = '';
    }
    if (!ALLOWED_HOSTS.has(host) && !blocked.includes(url)) blocked.push(url);
  }
  return blocked;
}
