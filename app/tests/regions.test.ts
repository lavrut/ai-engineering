import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { extractSection, listSections } from '../src/lib/regions';

const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8');
const py = read('../../harness/python/harness.py');
const go = read('../../harness/go/main.go');

describe('regions', () => {
  it('lists sections 1..9 in both files with matching titles', () => {
    const p = listSections(py);
    const g = listSections(go);
    expect(p.map((s) => s.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(g.map((s) => s.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(p.map((s) => s.title)).toEqual(g.map((s) => s.title));
    expect(p[7].title).toBe('The agent loop');
  });

  it('extracts the python agent loop', () => {
    const s = extractSection(py, 8);
    expect(s.startsWith('# ── 8. The agent loop')).toBe(true);
    expect(s).toContain('def run_agent');
    expect(s).not.toContain('── 9.');
    expect(s.endsWith('\n')).toBe(false);
  });

  it('extracts the go tool executor', () => {
    expect(extractSection(go, 5)).toContain('func executeTools');
  });

  it('throws for a missing section', () => {
    expect(() => extractSection(py, 10)).toThrow('Section 10 not found');
  });
});
