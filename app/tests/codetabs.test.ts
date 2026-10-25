import { describe, expect, it } from 'vitest';
import { pickLang } from '../src/lib/lang';

describe('pickLang', () => {
  it('defaults to python for null and unknown values', () => {
    expect(pickLang(null)).toBe('python');
    expect(pickLang('rust')).toBe('python');
  });
  it('accepts go', () => {
    expect(pickLang('go')).toBe('go');
  });
});
