// Create a lesson from templates/lesson.mdx.
// Usage: npm run new:lesson -- <track> <slug> "<Title>"
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SLUG_RE, lessonFileName, nextOrder, renderLesson } from './new-lesson-lib.mjs';

const app = join(dirname(fileURLToPath(import.meta.url)), '..');
const [track, slug, title] = process.argv.slice(2);

function fail(message) {
  console.error(message);
  process.exit(1);
}

if (!track || !slug || !title) fail('Usage: npm run new:lesson -- <track> <slug> "<Title>"');
if (!SLUG_RE.test(slug)) fail(`Slug "${slug}" must be lowercase words joined by hyphens.`);
if (!existsSync(join(app, 'src/content/tracks', `${track}.yaml`))) {
  fail(`No track "${track}". Create src/content/tracks/${track}.yaml from templates/track.yaml first.`);
}

const dir = join(app, 'src/content/lessons', track);
mkdirSync(dir, { recursive: true });
const existing = readdirSync(dir);
if (existing.some((f) => f.replace(/^\d{2}-/, '') === `${slug}.mdx`)) fail(`Track "${track}" already has a "${slug}" lesson.`);

const order = nextOrder(existing);
const file = join(dir, lessonFileName(order, slug));
const template = readFileSync(join(app, 'templates/lesson.mdx'), 'utf8');
writeFileSync(file, renderLesson(template, { track, order, slug, title }));
console.log(`Created ${file.slice(app.length + 1)}. Replace every TODO, then run npm test.`);
