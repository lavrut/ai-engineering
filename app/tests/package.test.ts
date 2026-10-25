import { describe, expect, it } from 'vitest';
import { findBlockedUrls, rewriteChunk, transformHtml } from '../scripts/package-lib.mjs';

const fixture = `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AI Engineering</title>
    <style>body{background:url(/assets/bg.svg)}</style>
    <script type="module" src="/assets/a.js"></script>
  </head>
  <body>
    <astro-island component-url="/assets/C.js" renderer-url="/assets/client.js"></astro-island>
  </body>
</html>`;

describe('transformHtml', () => {
  const out = transformHtml(fixture);

  it('drops the document wrapper tags', () => {
    for (const tag of [/<!doctype/i, /<html/i, /<\/html>/i, /<head/i, /<\/head>/i, /<body/i, /<\/body>/i]) {
      expect(out).not.toMatch(tag);
    }
  });

  it('drops charset and viewport metas', () => {
    expect(out).not.toMatch(/<meta charset/i);
    expect(out).not.toMatch(/name="viewport"/);
  });

  it('starts with the title', () => {
    expect(out.trimStart().startsWith('<title>AI Engineering</title>')).toBe(true);
  });

  it('keeps head content before body content', () => {
    expect(out.indexOf('<style>')).toBeLessThan(out.indexOf('<astro-island'));
  });

  it('keeps scripts Astro emits after </html>', () => {
    const late = transformHtml(`${fixture}<script type="module">route()</script>`);
    expect(late.trimEnd().endsWith('<script type="module">route()</script>')).toBe(true);
    expect(late).not.toMatch(/<\/html>|<\/body>/i);
  });

  // "./" matters: Astro hydrates with import(componentUrl), and import()
  // rejects a bare "assets/C.js" as an unresolvable module specifier.
  it('rewrites absolute /assets/ references to ./assets/', () => {
    expect(out).toContain('component-url="./assets/C.js"');
    expect(out).toContain('renderer-url="./assets/client.js"');
    expect(out).toContain('src="./assets/a.js"');
    expect(out).toContain('url(./assets/bg.svg)');
    expect(out).not.toMatch(/(?<!\.)\/assets\//);
  });

  it('contains no path segment starting with _astro', () => {
    expect(out).not.toMatch(/(?:^|[\s"'(=/])_astro/);
    expect(out).not.toContain('_astro');
  });
});

describe('rewriteChunk', () => {
  it('rewrites double-quoted dynamic imports', () => {
    expect(rewriteChunk('import("/assets/x.js")')).toBe('import("./x.js")');
  });

  it('rewrites single-quoted references', () => {
    expect(rewriteChunk("import('/assets/y.js')")).toBe("import('./y.js')");
  });
});

describe('findBlockedUrls', () => {
  it('reports scripts from hosts outside the allowlist', () => {
    expect(
      findBlockedUrls(
        '<script src="https://esm.sh/x"></script><link href="https://fonts.googleapis.com/css2">',
      ),
    ).toEqual(['https://esm.sh/x']);
  });

  it('ignores plain anchor links to other sites', () => {
    expect(findBlockedUrls('<a href="https://docs.anthropic.com">docs</a>')).toEqual([]);
  });

  it('reports blocked url() and import positions', () => {
    expect(
      findBlockedUrls(
        'a{background:url("https://evil.example/a.png")} import("https://cdn.example/m.js"); import x from "https://unpkg.com/x"',
      ),
    ).toEqual(['https://evil.example/a.png', 'https://cdn.example/m.js']);
  });

  it('reports protocol-relative URLs in every loading position', () => {
    expect(findBlockedUrls('<script src="//evil.example/x.js"></script>')).toEqual(['//evil.example/x.js']);
    expect(findBlockedUrls('<link rel=stylesheet href=//evil.example/a.css>')).toEqual(['//evil.example/a.css']);
    expect(findBlockedUrls('a{background:url(//evil.example/b.png)}')).toEqual(['//evil.example/b.png']);
    expect(findBlockedUrls('@import "//evil.example/c.css";')).toEqual(['//evil.example/c.css']);
    expect(findBlockedUrls('import("//evil.example/m.js")')).toEqual(['//evil.example/m.js']);
  });

  it('allows protocol-relative allowlisted hosts and ignores protocol-relative anchors', () => {
    expect(findBlockedUrls('<link href="//fonts.googleapis.com/css2">')).toEqual([]);
    expect(findBlockedUrls('<a href="//example.com/page">x</a>')).toEqual([]);
  });

  it('scans a tag past a > inside a quoted attribute', () => {
    expect(findBlockedUrls('<img alt="a > b" src="https://evil.example/i.png">')).toEqual([
      'https://evil.example/i.png',
    ]);
  });

  it('allows every allowlisted host', () => {
    const hosts = [
      'fonts.googleapis.com',
      'fonts.gstatic.com',
      'cdnjs.cloudflare.com',
      'cdn.jsdelivr.net',
      'unpkg.com',
    ];
    expect(findBlockedUrls(hosts.map((h) => `<script src="https://${h}/x.js"></script>`).join(''))).toEqual([]);
  });
});
