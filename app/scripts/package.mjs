// dist/ -> publish/: the page-host layout (index.html without a document
// skeleton, chunks beside it under assets/ with relative references).
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { findBlockedUrls, rewriteChunk, transformHtml } from './package-lib.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const publish = join(root, 'publish');
const FIT_SIDES = '<meta name="claude:viewport-fit-sides" content="cover">';

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

if (!existsSync(join(dist, 'index.html'))) {
  console.error('dist/index.html not found: run npm run build first.');
  process.exit(1);
}

rmSync(publish, { recursive: true, force: true });
mkdirSync(publish, { recursive: true });

// Astro names script chunks like "X.astro_astro_type_script_...js"; the
// "_astro" inside would trip naive reserved-name checks, so rename it.
const clean = (t) => t.replaceAll('_astro', '-astro');

const blocked = [];
for (const src of walk(dist)) {
  const rel = relative(dist, src);
  const dest = join(publish, clean(rel));
  mkdirSync(dirname(dest), { recursive: true });

  if (rel === 'index.html') {
    let html = clean(transformHtml(readFileSync(src, 'utf8')));
    if (!html.includes('name="claude:viewport-fit-sides"')) {
      html = html.replace(/(<title\b[^>]*>[\s\S]*?<\/title>)/i, `$1\n${FIT_SIDES}`);
    }
    blocked.push(...findBlockedUrls(html).map((u) => `${rel}: ${u}`));
    writeFileSync(dest, html);
  } else if (rel.endsWith('.js')) {
    const js = clean(rewriteChunk(readFileSync(src, 'utf8')));
    blocked.push(...findBlockedUrls(js).map((u) => `${rel}: ${u}`));
    writeFileSync(dest, js);
  } else {
    cpSync(src, dest);
  }
}

const reserved = walk(publish)
  .map((p) => relative(publish, p))
  .filter((rel) => rel.split(/[\\/]/)[0].startsWith('_'));
if (reserved.length > 0) {
  console.error('Published paths whose first segment starts with "_" are reserved by the page host:');
  for (const r of reserved) console.error(`  ${r}`);
  process.exit(1);
}

if (blocked.length > 0) {
  console.error('Blocked external URLs (not on the page-host allowlist):');
  for (const b of blocked) console.error(`  ${b}`);
  process.exit(1);
}
console.log(`Packaged ${relative(root, dist)}/ -> ${relative(root, publish)}/`);
