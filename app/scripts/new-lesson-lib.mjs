// Pure helpers for scripts/new-lesson.mjs, kept separate so tests can import them.

/** Slugs are lowercase words joined by hyphens: "agent-loop". */
export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Lesson files are named NN-slug.mdx. */
const FILE_RE = /^(\d{2})-[a-z0-9-]+\.mdx$/;

/** The order for a new lesson: one past the highest NN among existing files. */
export function nextOrder(fileNames) {
  const orders = fileNames.map((f) => f.match(FILE_RE)).filter(Boolean).map((m) => Number(m[1]));
  return orders.length ? Math.max(...orders) + 1 : 1;
}

/** NN-slug.mdx for an order and slug. */
export function lessonFileName(order, slug) {
  return `${String(order).padStart(2, '0')}-${slug}.mdx`;
}

/** Fill the template's {{track}}, {{order}}, {{slug}} and {{title}} placeholders. */
export function renderLesson(template, { track, order, slug, title }) {
  const values = { track, order: String(order), slug, title };
  return template.replace(/\{\{(track|order|slug|title)\}\}/g, (_, key) => values[key]);
}
