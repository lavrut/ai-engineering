// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/preact';
import FlowDiagram, { routeEdges } from '../src/components/FlowDiagram';
import { DEFAULT_NODE_H, DEFAULT_NODE_W, type Diagram } from '../src/lib/diagram';
import { LANG_EVENT } from '../src/lib/lang';
import { evalPipeline } from '../src/data/diagrams/eval-pipeline';
import { harnessFlow } from '../src/data/diagrams/harness-flow';
import { requestResponse } from '../src/data/diagrams/request-response';
import { toolRoundtrip } from '../src/data/diagrams/tool-roundtrip';

const codePython = { 4: 'PY-4', 5: 'PY-5', 6: 'PY-6', 7: 'PY-7', 8: 'PY-8', 9: 'PY-9' };
const codeGo = { 4: 'GO-4', 5: 'GO-5', 6: 'GO-6', 7: 'GO-7', 8: 'GO-8', 9: 'GO-9' };

function setup(mode?: 'replay') {
  const view = render(<FlowDiagram diagram={harnessFlow} codePython={codePython} codeGo={codeGo} mode={mode} />);
  const status = () => view.container.querySelector('.fd-status')?.textContent;
  const caption = () => view.container.querySelector('.fd-caption')?.textContent;
  return { ...view, status, caption };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('FlowDiagram stepping', () => {
  it('starts at step 1', () => {
    const { status, caption } = setup();
    expect(status()).toBe('Step 1 of 10');
    expect(caption()).toBe(harnessFlow.steps[0].caption);
  });

  it('Next twice goes to step 3', () => {
    const { status } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(status()).toBe('Step 3 of 10');
  });

  it('Reset returns to step 1', () => {
    const { status } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(status()).toBe('Step 1 of 10');
  });

  it('Prev at step 1 stays on step 1', () => {
    const { status } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(status()).toBe('Step 1 of 10');
  });

  it('clicking step 7 in the list shows step 7 and its caption', () => {
    const { status, caption, container } = setup();
    const item = container.querySelectorAll<HTMLButtonElement>('.fd-steps button')[6];
    fireEvent.click(item);
    expect(status()).toBe('Step 7 of 10');
    expect(caption()).toBe(harnessFlow.steps[6].caption);
    expect(item.getAttribute('aria-current')).toBe('step');
  });

  it('arrow keys step when the figure has focus', () => {
    const { status, container } = setup();
    const canvas = container.querySelector<HTMLElement>('.fd-canvas')!;
    canvas.focus();
    fireEvent.keyDown(canvas, { key: 'ArrowRight' });
    fireEvent.keyDown(canvas, { key: 'ArrowRight' });
    fireEvent.keyDown(canvas, { key: 'ArrowLeft' });
    expect(status()).toBe('Step 2 of 10');
  });

  it('highlights the nodes and edges of the current step', () => {
    const { container } = setup();
    const on = Array.from(container.querySelectorAll('svg .is-on[data-id]')).map((el) => el.getAttribute('data-id'));
    expect(on.sort()).toEqual([...harnessFlow.steps[0].highlight].sort());
  });

  it('Play advances every 2.2s and stops at the end', () => {
    vi.useFakeTimers();
    const { status } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    act(() => {
      vi.advanceTimersByTime(2200);
    });
    expect(status()).toBe('Step 2 of 10');
    act(() => {
      vi.advanceTimersByTime(2200 * 20);
    });
    expect(status()).toBe('Step 10 of 10');
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
  });
});

describe('FlowDiagram code panel', () => {
  it('Show code reveals the linked section in the stored language', () => {
    const { container } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Next' })); // step 2 -> section 6
    const toggle = screen.getByRole('button', { name: 'Show code' });
    expect(container.querySelector('.fd-code pre')).toBeNull();
    fireEvent.click(toggle);
    expect(container.querySelector('.fd-code pre')?.textContent).toBe('PY-6');
    act(() => {
      window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: 'go' }));
    });
    expect(container.querySelector('.fd-code pre')?.textContent).toBe('GO-6');
  });

  it('reads aie:lang on mount', () => {
    // Node's own (file-less) localStorage shadows jsdom's, so stub a tiny one.
    vi.stubGlobal('localStorage', { getItem: (k: string) => (k === 'aie:lang' ? 'go' : null) });
    const { container } = setup('replay');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(container.querySelector('.fd-code pre')?.textContent).toBe('GO-6');
  });

  it('replay mode opens the code panel by default', () => {
    const { container } = setup('replay');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByRole('button', { name: 'Hide code' })).toBeTruthy();
    expect(container.querySelector('.fd-code pre')?.textContent).toBe('PY-6');
  });

  it('labels harness code with the harness file names', () => {
    const { container } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(screen.getByRole('button', { name: 'Show code' }));
    expect(container.querySelector('.fd-code-label')?.textContent).toBe('harness.py · section 6');
  });

  it('labels and picks code by the step source (llms)', () => {
    const view = render(
      <FlowDiagram diagram={evalPipeline} codePython={{ 'llms:2': 'LLMS-PY-2' }} codeGo={{ 'llms:2': 'LLMS-GO-2' }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Show code' }));
    expect(view.container.querySelector('.fd-code-label')?.textContent).toBe('triage_lab.py · section 2');
    expect(view.container.querySelector('.fd-code pre')?.textContent).toBe('LLMS-PY-2');
    act(() => {
      window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: 'go' }));
    });
    expect(view.container.querySelector('.fd-code-label')?.textContent).toBe('main.go · section 2');
    expect(view.container.querySelector('.fd-code pre')?.textContent).toBe('LLMS-GO-2');
  });

  it('falls back to Python for a Python-only source', () => {
    const lora: Diagram = {
      id: 'lora-test',
      title: 'LoRA',
      width: 400,
      height: 200,
      nodes: [
        { id: 'a', label: 'A', role: 'dataset', x: 10, y: 10 },
        { id: 'b', label: 'B', role: 'grader', x: 200, y: 10 },
      ],
      edges: [{ id: 'e', from: 'a', to: 'b' }],
      steps: [{ n: 1, highlight: ['a', 'e', 'b'], caption: 'x', code: { file: 'python', section: 3, source: 'lora' } }],
    };
    vi.stubGlobal('localStorage', { getItem: (k: string) => (k === 'aie:lang' ? 'go' : null) });
    const view = render(<FlowDiagram diagram={lora} codePython={{ 'lora:3': 'LORA-3' }} codeGo={{}} mode="replay" />);
    expect(view.container.querySelector('.fd-code-label')?.textContent).toBe('lora.py · section 3');
    expect(view.container.querySelector('.fd-code pre')?.textContent).toBe('LORA-3');
    expect(view.container.querySelector('.fd-legend')?.textContent).toContain('Dataset');
    expect(view.container.querySelector('.fd-legend')?.textContent).toContain('Grader');
  });

  it('disables Show code on a step with no linked section', () => {
    setup();
    expect((screen.getByRole('button', { name: 'Show code' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

// ── Edge routing: orthogonal paths, no crossings, no edge through a node ──
type Pt = [number, number];
type Seg = { a: Pt; b: Pt };

function segments(points: Pt[]): Seg[] {
  const out: Seg[] = [];
  for (let i = 1; i < points.length; i++) out.push({ a: points[i - 1], b: points[i] });
  return out;
}
const isH = (s: Seg) => s.a[1] === s.b[1];
const between = (v: number, p: number, q: number) => v > Math.min(p, q) + 0.5 && v < Math.max(p, q) - 0.5;

function crosses(s: Seg, t: Seg): boolean {
  if (isH(s) === isH(t)) return false; // parallel or collinear (shared trunks are allowed)
  const [h, v] = isH(s) ? [s, t] : [t, s];
  return between(h.a[1], v.a[1], v.b[1]) && between(v.a[0], h.a[0], h.b[0]);
}

function throughBox(s: Seg, n: Diagram['nodes'][number]): boolean {
  const w = n.w ?? DEFAULT_NODE_W;
  const h = n.h ?? DEFAULT_NODE_H;
  if (isH(s)) return between(s.a[1], n.y, n.y + h) && Math.max(s.a[0], s.b[0]) > n.x + 0.5 && Math.min(s.a[0], s.b[0]) < n.x + w - 0.5;
  return between(s.a[0], n.x, n.x + w) && Math.max(s.a[1], s.b[1]) > n.y + 0.5 && Math.min(s.a[1], s.b[1]) < n.y + h - 0.5;
}

describe.each([
  ['harness-flow', harnessFlow],
  ['request-response', requestResponse],
  ['tool-roundtrip', toolRoundtrip],
  ['eval-pipeline', evalPipeline],
])('routeEdges(%s)', (_name, d) => {
  const routes = routeEdges(d);

  it('routes every edge with orthogonal segments inside the canvas', () => {
    for (const e of d.edges) {
      const pts = routes[e.id].points;
      expect(pts.length).toBeGreaterThanOrEqual(2);
      for (const s of segments(pts)) expect(s.a[0] === s.b[0] || s.a[1] === s.b[1]).toBe(true);
      for (const [x, y] of pts) {
        expect(x).toBeGreaterThanOrEqual(0);
        expect(y).toBeGreaterThanOrEqual(0);
        expect(x).toBeLessThanOrEqual(d.width);
        expect(y).toBeLessThanOrEqual(d.height);
      }
    }
  });

  it('has no crossing edges', () => {
    const problems: string[] = [];
    d.edges.forEach((e, i) =>
      d.edges.slice(i + 1).forEach((f) => {
        for (const s of segments(routes[e.id].points))
          for (const t of segments(routes[f.id].points)) if (crosses(s, t)) problems.push(`${e.id} x ${f.id}`);
      }),
    );
    expect(problems).toEqual([]);
  });

  it('gives every label on a horizontal segment room between the nodes', () => {
    const problems: string[] = [];
    for (const e of d.edges) {
      const r = routes[e.id];
      if (!e.label || !r.horizontal) continue;
      const seg = segments(r.points).find((s) => isH(s) && (s.a[0] + s.b[0]) / 2 === r.anchor[0] && s.a[1] === r.anchor[1])!;
      const room = Math.abs(seg.b[0] - seg.a[0]);
      const need = e.label.length * 7.2 + 6; // 12px mono + halo
      if (need > room) problems.push(`${e.id}: "${e.label}" needs ${need}, has ${room}`);
    }
    expect(problems).toEqual([]);
  });

  it('never draws an edge through a node it does not connect', () => {
    const problems: string[] = [];
    for (const e of d.edges)
      for (const n of d.nodes) {
        if (n.id === e.from || n.id === e.to) continue;
        if (segments(routes[e.id].points).some((s) => throughBox(s, n))) problems.push(`${e.id} through ${n.id}`);
      }
    expect(problems).toEqual([]);
  });
});
