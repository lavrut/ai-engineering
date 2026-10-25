export type Lang = 'python' | 'go';

/** Narrow a stored `aie:lang` value to a known language; anything else is Python. */
export function pickLang(stored: string | null): Lang {
  return stored === 'go' ? 'go' : 'python';
}
