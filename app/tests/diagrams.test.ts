import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { validateDiagram, type Diagram } from '../src/lib/diagram';
import { listSections } from '../src/lib/regions';
import { evalPipeline } from '../src/data/diagrams/eval-pipeline';
import { harnessFlow } from '../src/data/diagrams/harness-flow';
import { requestResponse } from '../src/data/diagrams/request-response';
import { toolRoundtrip } from '../src/data/diagrams/tool-roundtrip';

const repo = join(dirname(fileURLToPath(import.meta.url)), '../..');
// The files a step's code link can show, by `code.source` (default harness).
const SOURCE_FILES: Record<string, string[]> = {
  harness: ['harness/python/harness.py', 'harness/go/main.go'],
  llms: ['llms/python/triage_lab.py', 'llms/go/main.go'],
  lora: ['llms/finetune/lora.py'],
};
const sectionsIn = (file: string) => listSections(readFileSync(join(repo, file), 'utf8')).map((s) => s.n);

const all: [string, Diagram][] = [
  ['harness-flow', harnessFlow],
  ['request-response', requestResponse],
  ['tool-roundtrip', toolRoundtrip],
  ['eval-pipeline', evalPipeline],
];

describe('diagram data', () => {
  it.each(all)('%s validates cleanly', (_name, d) => {
    expect(validateDiagram(d)).toEqual([]);
  });

  it('harness-flow has 10 steps and exactly one loop edge (9 -> 3)', () => {
    expect(harnessFlow.steps).toHaveLength(10);
    const loops = harnessFlow.edges.filter((e) => e.loop);
    expect(loops).toHaveLength(1);
    expect(loops[0].from).toBe('context-manager');
    expect(loops[0].to).toBe('context-builder');
  });

  it('harness-flow links steps to the right code sections', () => {
    const sec = Object.fromEntries(harnessFlow.steps.map((s) => [s.n, s.code?.section]));
    expect(sec).toMatchObject({ 2: 6, 4: 6, 5: 8, 6: 4, 7: 5, 8: 8, 9: 7, 10: 9 });
  });

  it.each(all)('%s: every step code link names a section in its source', (_name, d) => {
    const problems: string[] = [];
    for (const s of d.steps) {
      if (!s.code) continue;
      const files = SOURCE_FILES[s.code.source ?? 'harness'];
      if (!files) problems.push(`step ${s.n}: unknown source ${s.code.source}`);
      for (const f of files ?? []) {
        if (!sectionsIn(f).includes(s.code.section)) problems.push(`step ${s.n}: no section ${s.code.section} in ${f}`);
      }
    }
    expect(problems).toEqual([]);
  });

  it('eval-pipeline has the 8 eval steps, linked to llms sections', () => {
    expect(evalPipeline.steps).toHaveLength(8);
    for (const s of evalPipeline.steps) if (s.code) expect(s.code.source).toBe('llms');
    const sec = Object.fromEntries(evalPipeline.steps.map((s) => [s.n, s.code?.section]));
    expect(sec).toMatchObject({ 1: 2, 2: 3, 3: 4, 4: 4, 5: 5, 6: 6, 7: 9, 8: 10 });
    const roles = new Set(evalPipeline.nodes.map((n) => n.role));
    expect(roles.has('dataset')).toBe(true);
    expect(roles.has('grader')).toBe(true);
  });

  it.each(all)('%s: every highlighted node is reached by a highlighted edge', (_name, d) => {
    const problems: string[] = [];
    for (const s of d.steps) {
      const edges = d.edges.filter((e) => s.highlight.includes(e.id));
      for (const n of d.nodes.filter((n) => s.highlight.includes(n.id))) {
        if (!edges.some((e) => e.from === n.id || e.to === n.id)) problems.push(`step ${s.n}: ${n.id}`);
      }
    }
    expect(problems).toEqual([]);
  });

  it('lesson diagrams have 4 to 6 steps', () => {
    for (const d of [requestResponse, toolRoundtrip]) {
      expect(d.steps.length).toBeGreaterThanOrEqual(4);
      expect(d.steps.length).toBeLessThanOrEqual(6);
    }
  });

  it('reports errors for a broken diagram', () => {
    const bad: Diagram = {
      id: 'bad',
      title: 'Bad',
      width: 100,
      height: 100,
      nodes: [
        { id: 'a', label: 'A', role: 'user', x: 0, y: 0 },
        { id: 'a', label: 'dup', role: 'user', x: 500, y: 0 },
      ],
      edges: [{ id: 'e1', from: 'a', to: 'ghost' }],
      steps: [
        { n: 1, highlight: ['a'], caption: 'x' },
        { n: 3, highlight: ['nope'], caption: 'y', code: { file: 'go', section: 10 } },
      ],
    };
    expect(validateDiagram(bad).length).toBeGreaterThanOrEqual(2);
  });
});
