// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { THEME_KEY, applyTheme, pickTheme, readTheme, saveTheme, watchHostTheme } from '../src/lib/theme';

const root = document.documentElement;

// Node's own localStorage global shadows jsdom's, so give each test a fresh one.
beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  root.removeAttribute('data-theme');
  root.removeAttribute('data-host-theme');
});

describe('pickTheme', () => {
  it('accepts light and dark', () => {
    expect(pickTheme('light')).toBe('light');
    expect(pickTheme('dark')).toBe('dark');
  });
  it('treats null and unknown values as auto', () => {
    expect(pickTheme(null)).toBe('auto');
    expect(pickTheme('sepia')).toBe('auto');
  });
});

describe('applyTheme', () => {
  it('pins light or dark on the root', () => {
    applyTheme('light');
    expect(root.getAttribute('data-theme')).toBe('light');
    applyTheme('dark');
    expect(root.getAttribute('data-theme')).toBe('dark');
  });
  it('auto removes the attribute when the host set none', () => {
    root.setAttribute('data-theme', 'dark');
    applyTheme('auto');
    expect(root.hasAttribute('data-theme')).toBe(false);
  });
  it("auto restores the host's theme", () => {
    root.setAttribute('data-host-theme', 'dark');
    applyTheme('light');
    applyTheme('auto');
    expect(root.getAttribute('data-theme')).toBe('dark');
  });
});

describe('saveTheme', () => {
  it('stores the choice and applies it', () => {
    saveTheme('light');
    expect(localStorage.getItem(THEME_KEY)).toBe('light');
    expect(readTheme()).toBe('light');
    expect(root.getAttribute('data-theme')).toBe('light');
  });
});

describe('watchHostTheme', () => {
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it("re-applies the reader's choice when the host changes the theme", async () => {
    saveTheme('light');
    const stop = watchHostTheme();
    root.setAttribute('data-theme', 'dark');
    await flush();
    stop();
    expect(root.getAttribute('data-theme')).toBe('light');
    applyTheme('auto');
    expect(root.getAttribute('data-theme')).toBe('dark');
  });

  it('follows the host while the choice is auto', async () => {
    const stop = watchHostTheme();
    root.setAttribute('data-theme', 'dark');
    await flush();
    stop();
    expect(root.getAttribute('data-theme')).toBe('dark');
    expect(root.getAttribute('data-host-theme')).toBe('dark');
  });

  it('catches a host change made before it started', () => {
    localStorage.setItem(THEME_KEY, 'light');
    root.setAttribute('data-theme', 'dark');
    const stop = watchHostTheme();
    stop();
    expect(root.getAttribute('data-theme')).toBe('light');
    expect(root.getAttribute('data-host-theme')).toBe('dark');
  });
});
