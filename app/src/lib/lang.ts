export type Lang = 'python' | 'go';

/** localStorage key holding the reader's language choice. */
export const LANG_KEY = 'aie:lang';
/** Window CustomEvent (detail: Lang) fired whenever the reader picks a language. */
export const LANG_EVENT = 'aie:lang';

/** Narrow a stored `aie:lang` value to a known language; anything else is Python. */
export function pickLang(stored: string | null): Lang {
  return stored === 'go' ? 'go' : 'python';
}

/** The stored language, or Python when storage is empty or unavailable. */
export function readLang(): Lang {
  try {
    return pickLang(localStorage.getItem(LANG_KEY));
  } catch {
    return 'python';
  }
}
