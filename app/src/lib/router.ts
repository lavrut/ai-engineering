/** View id for a lesson: `<track>-<slug>`, used as the bare hash token. */
export function viewId(track: string, slug: string): string {
  return `${track}-${slug}`;
}

/** Map a location hash to a known view id, falling back to `home`. */
export function resolveView(hash: string, known: Set<string>): string {
  const token = hash.replace(/^#/, '');
  return token && known.has(token) ? token : 'home';
}
