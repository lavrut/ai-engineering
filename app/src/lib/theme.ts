export type ThemeChoice = 'light' | 'dark' | 'auto';

/** localStorage key holding the reader's theme choice. */
export const THEME_KEY = 'aie:theme';
/**
 * Where the page keeps the theme the host asked for (the host page may set
 * data-theme itself), so "Auto" can hand control back to it.
 */
const HOST_ATTR = 'data-host-theme';

/** Narrow a stored `aie:theme` value; anything unknown is Auto. */
export function pickTheme(stored: string | null): ThemeChoice {
  return stored === 'light' || stored === 'dark' ? stored : 'auto';
}

/** The stored choice, or Auto when storage is empty or unavailable. */
export function readTheme(): ThemeChoice {
  try {
    return pickTheme(localStorage.getItem(THEME_KEY));
  } catch {
    return 'auto';
  }
}

/**
 * Set data-theme for a choice. Light and Dark pin the attribute; Auto restores
 * whatever the host set, or removes it so the system preference decides.
 */
export function applyTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement): void {
  if (choice !== 'auto') {
    root.setAttribute('data-theme', choice);
    return;
  }
  const host = root.getAttribute(HOST_ATTR);
  if (host) root.setAttribute('data-theme', host);
  else root.removeAttribute('data-theme');
}

/** Store and apply the reader's choice. */
export function saveTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement): void {
  try {
    localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Storage blocked: the choice still applies for this visit.
  }
  applyTheme(choice, root);
}

/**
 * Keep the reader's choice in force if the host changes data-theme after load,
 * including any change made before this watcher started. A host change is
 * remembered so switching back to Auto follows the host.
 * Returns a function that stops watching.
 */
export function watchHostTheme(root: HTMLElement = document.documentElement): () => void {
  const reconcile = () => {
    const choice = readTheme();
    const current = root.getAttribute('data-theme');
    if (choice === 'auto') {
      if (current) root.setAttribute(HOST_ATTR, current);
      else root.removeAttribute(HOST_ATTR);
    } else if (current !== choice) {
      if (current) root.setAttribute(HOST_ATTR, current);
      applyTheme(choice, root);
    }
  };
  reconcile();
  if (typeof MutationObserver === 'undefined') return () => {};
  const observer = new MutationObserver(reconcile);
  observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  return () => observer.disconnect();
}
