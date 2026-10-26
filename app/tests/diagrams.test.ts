import { describe, expect, it } from 'vitest';
import { codeProblems } from '../src/lib/codeSourceText';
import { codeKey, validateDiagram, type Diagram } from '../src/lib/diagram';
import { evalPipeline } from '../src/data/diagrams/eval-pipeline';
import { inspectPipeline } from '../src/data/diagrams/inspect-pipeline';
import { harnessFlow } from '../src/data/diagrams/harness-flow';
import { mlLoop } from '../src/data/diagrams/ml-loop';
import { mlPipeline } from '../src/data/diagrams/ml-pipeline';
import { requestResponse } from '../src/data/diagrams/request-response';
import { toolRoundtrip } from '../src/data/diagrams/tool-roundtrip';

const all: [string, Diagram][] = [
  ['harness-flow', harnessFlow],
  ['request-response', requestResponse],
  ['tool-roundtrip', toolRoundtrip],
  ['eval-pipeline', evalPipeline],
  ['inspect-pipeline', inspectPipeline],
  ['ml-pipeline', mlPipeline],
  ['ml-loop', mlLoop],
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
    // The same check as a lesson's CodeTabs: Python's section, and Go's (goSection where the Go file numbers its own).
    const problems = d.steps.flatMap((s) => (s.code ? codeProblems(s.code).map((p) => `step ${s.n}: ${p}`) : []));
    expect(problems).toEqual([]);
  });

  it('checks a step code link against both of its files', () => {
    const step = (code: object) => codeProblems(code as { section: number });
    expect(step({ file: 'python', section: 4, source: 'ml-serve', goSection: 5 })).toEqual([]);
    expect(step({ file: 'python', section: 4, source: 'ml-serve' })).toHaveLength(1);
    expect(step({ file: 'python', section: 9, source: 'ml-train' })).toHaveLength(1);
  });

  it('keys a step with a goSection apart from one without', () => {
    expect(codeKey({ section: 6 })).toBe('6');
    expect(codeKey({ section: 6, source: 'llms' })).toBe('llms:6');
    expect(codeKey({ section: 4, source: 'ml-serve', goSection: 5 })).toBe('ml-serve:4:go5');
  });

  it('ml-pipeline follows one complaint to a label, linked to train.py', () => {
    expect(mlPipeline.nodes.map((n) => n.id)).toEqual(['complaint', 'tokenizer', 'tfidf', 'model-file', 'scores', 'label']);
    expect(mlPipeline.steps.length).toBeGreaterThanOrEqual(5);
    for (const s of mlPipeline.steps) if (s.code) expect(s.code.source).toBe('ml-train');
    const sec = Object.fromEntries(mlPipeline.steps.map((s) => [s.n, s.code?.section]));
    expect(sec).toMatchObject({ 2: 3, 3: 3 });
  });

  it('ml-loop runs serve, monitor, retrain, gate, promote, and can roll back', () => {
    const ids = mlLoop.nodes.map((n) => n.id);
    for (const id of ['server', 'monitor', 'retrain', 'gate', 'registry']) expect(ids).toContain(id);
    const loops = mlLoop.edges.filter((e) => e.loop);
    expect(loops.map((e) => [e.from, e.to])).toEqual([['registry', 'server']]);
    expect(mlLoop.edges.some((e) => e.id === 'e-rollback')).toBe(true);
    expect(mlLoop.steps.at(-1)!.highlight).toContain('e-rollback');
    const sources = new Set(mlLoop.steps.map((s) => s.code?.source));
    for (const s of ['ml-serve', 'ml-monitor', 'ml-mlops']) expect(sources.has(s as never)).toBe(true);
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

  it('inspect-pipeline has 5 steps linked to llms-inspect sections', () => {
    expect(inspectPipeline.steps).toHaveLength(5);
    for (const s of inspectPipeline.steps) expect(s.code?.source).toBe('llms-inspect');
    const sec = Object.fromEntries(inspectPipeline.steps.map((s) => [s.n, s.code?.section]));
    expect(sec).toEqual({ 1: 2, 2: 4, 3: 3, 4: 5, 5: 7 });
    expect(inspectPipeline.nodes.find((n) => n.id === 'replay')?.label).toBe('Model (replay provider)');
    expect(inspectPipeline.nodes.map((n) => n.id)).toEqual(['dataset', 'solver', 'replay', 'scorers', 'log']);
  });

  it('inspect-pipeline captions name the right lab sections', () => {
    const lab = inspectPipeline.steps.map((s) => {
      const m = /In the lab: sections? (\d+)(?: and (\d+))?/.exec(s.caption);
      return m ? [Number(m[1]), ...(m[2] ? [Number(m[2])] : [])] : null;
    });
    expect(lab).toEqual([[2], [3, 4], [4], [5], [8, 9]]);
    expect(inspectPipeline.steps[3].caption).toContain('section 6');
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
