import { useEffect, useState } from 'preact/hooks';
import {
  codeKey,
  DEFAULT_NODE_H,
  DEFAULT_NODE_W,
  type CodeSource,
  type DEdge,
  type DNode,
  type Diagram,
  type Role,
} from '../lib/diagram';
import { LANG_EVENT, pickLang, readLang, type Lang } from '../lib/lang';

type Props = {
  diagram: Diagram;
  /** Source sections keyed by codeKey ("6" for the harness, "llms:6" for the lab; only those the steps use). */
  codePython: Record<string, string>;
  codeGo: Record<string, string>;
  /** "replay": the code panel starts open (capstone). */
  mode?: 'replay';
};

const PLAY_MS = 2200;
const ROLE_LABEL: Record<Role, string> = {
  user: 'User',
  harness: 'Harness',
  model: 'Model',
  tool: 'Tool',
  memory: 'Memory',
  logs: 'Logs',
  dataset: 'Dataset',
  grader: 'Grader',
};
/** The file each source shows per language; LoRA is Python only. */
const FILE_NAME: Record<CodeSource, Partial<Record<Lang, string>>> = {
  harness: { python: 'harness.py', go: 'main.go' },
  llms: { python: 'triage_lab.py', go: 'main.go' },
  lora: { python: 'lora.py' },
};

export default function FlowDiagram({ diagram, codePython, codeGo, mode }: Props) {
  const steps = diagram.steps;
  const last = steps.length - 1;
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [showCode, setShowCode] = useState(mode === 'replay');
  const [lang, setLang] = useState<Lang>('python');

  useEffect(() => {
    setLang(readLang());
    const onLang = (e: Event) => setLang(pickLang((e as CustomEvent<string>).detail ?? null));
    window.addEventListener(LANG_EVENT, onLang);
    return () => window.removeEventListener(LANG_EVENT, onLang);
  }, []);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setIndex((i) => Math.min(i + 1, last)), PLAY_MS);
    return () => clearInterval(id);
  }, [playing, last]);

  useEffect(() => {
    if (playing && index === last) setPlaying(false);
  }, [playing, index, last]);

  function go(i: number) {
    setPlaying(false);
    setIndex(Math.max(0, Math.min(last, i)));
  }

  function togglePlay() {
    if (playing) return setPlaying(false);
    if (index === last) setIndex(0);
    setPlaying(true);
  }

  function onKeyDown(e: KeyboardEvent) {
    if ((e.target as HTMLElement | null)?.closest('pre')) return;
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      go(index + 1);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      go(index - 1);
    }
  }

  const step = steps[index];
  const on = new Set(step.highlight);
  const routes = routeEdges(diagram);
  const roles = Array.from(new Set(diagram.nodes.map((n) => n.role)));
  const section = step.code?.section;
  const source = step.code?.source ?? 'harness';
  const key = step.code ? codeKey(step.code) : undefined;
  // A Python-only source (or a missing Go section) shows the Python code even when Go is chosen.
  const codeLang: Lang = lang === 'go' && key !== undefined && codeGo[key] === undefined ? 'python' : lang;
  const code = key === undefined ? undefined : (codeLang === 'go' ? codeGo : codePython)[key];
  const codeId = `${diagram.id}-code`;
  const arrow = `${diagram.id}-arrow`;

  return (
    <figure class="flowdiagram" onKeyDown={onKeyDown}>
      <figcaption class="fd-head">
        <span class="fd-title">{diagram.title}</span>
        <ul class="fd-legend" aria-label="Roles">
          {roles.map((r) => (
            <li key={r}>
              <span class="fd-swatch" style={{ background: `var(--role-${r})` }} aria-hidden="true" />
              {ROLE_LABEL[r]}
            </li>
          ))}
        </ul>
      </figcaption>

      <div
        class="fd-canvas"
        tabIndex={0}
        role="group"
        aria-label={`${diagram.title}. Use the left and right arrow keys to step.`}
      >
        <svg
          viewBox={`0 0 ${diagram.width} ${diagram.height}`}
          style={{ minWidth: `${Math.round(diagram.width * 0.72)}px` }}
          role="img"
          aria-label={`${diagram.title}: step ${step.n} of ${steps.length}`}
        >
          <defs>
            <marker id={arrow} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" class="fd-head-dim" />
            </marker>
            <marker id={`${arrow}-on`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" class="fd-head-on" />
            </marker>
          </defs>

          {diagram.edges.map((e) => {
            const r = routes[e.id];
            const lit = on.has(e.id);
            return (
              <g key={e.id} data-id={e.id} class={`fd-edge${lit ? ' is-on' : ''}${e.loop ? ' is-loop' : ''}`}>
                <path
                  d={`M${r.points.map((p) => p.join(' ')).join(' L')}`}
                  fill="none"
                  marker-end={`url(#${lit ? `${arrow}-on` : arrow})`}
                />
                {e.label && <EdgeLabel route={r} text={e.label} />}
              </g>
            );
          })}

          {diagram.nodes.map((n) => (
            <NodeBox key={n.id} node={n} lit={on.has(n.id)} />
          ))}

          {steps.map((s, i) => {
            const at = badgeAt(diagram, routes, s.highlight);
            if (!at) return null;
            const state = i === index ? ' is-current' : i < index ? ' is-done' : '';
            return (
              <g key={s.n} class={`fd-badge${state}`} aria-hidden="true">
                <circle cx={at[0]} cy={at[1]} r={11} />
                <text x={at[0]} y={at[1] + 4} text-anchor="middle">
                  {s.n}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <div class="fd-controls">
        <button type="button" class="fd-btn" onClick={() => go(index - 1)} disabled={index === 0}>
          Previous
        </button>
        <button type="button" class="fd-btn fd-btn-primary" onClick={() => go(index + 1)} disabled={index === last}>
          Next
        </button>
        <button type="button" class="fd-btn" onClick={togglePlay}>
          {playing ? 'Pause' : 'Play'}
        </button>
        <button type="button" class="fd-btn" onClick={() => go(0)}>
          Reset
        </button>
      </div>

      <div class="fd-body">
        <div class="fd-now">
          <div aria-live="polite">
            <p class="fd-status">{`Step ${step.n} of ${steps.length}`}</p>
            <p class="fd-caption">{step.caption}</p>
          </div>
          <button
            type="button"
            class="fd-btn fd-code-toggle"
            aria-expanded={showCode && code !== undefined}
            aria-controls={codeId}
            disabled={code === undefined}
            onClick={() => setShowCode(!showCode)}
          >
            {showCode && code !== undefined ? 'Hide code' : 'Show code'}
          </button>
          {showCode && code !== undefined && (
            <div class="fd-code" id={codeId}>
              <p class="fd-code-label">{`${FILE_NAME[source][codeLang]} · section ${section}`}</p>
              <pre tabIndex={0}>{code}</pre>
            </div>
          )}
        </div>

        <ol class="fd-steps" aria-label="Steps">
          {steps.map((s, i) => (
            <li key={s.n}>
              <button type="button" aria-current={i === index ? 'step' : undefined} onClick={() => go(i)}>
                <span class="fd-steps-n">{s.n}</span>
                <span class="fd-steps-text">{s.caption}</span>
              </button>
            </li>
          ))}
        </ol>
      </div>
      <style>{css}</style>
    </figure>
  );
}

function NodeBox({ node, lit }: { node: DNode; lit: boolean }) {
  const b = box(node);
  const cx = b.x + b.w / 2;
  const tag = ROLE_LABEL[node.role].toUpperCase();
  const tagW = tag.length * 7.3; // 11px mono + 0.06em tracking
  const color = `var(--role-${node.role})`;
  return (
    <g data-id={node.id} class={`fd-node${lit ? ' is-on' : ''}`}>
      <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={6} class="fd-node-box" style={{ stroke: color }} />
      <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={6} class="fd-node-tint" style={{ fill: color }} />
      <rect x={cx - tagW / 2 - 13} y={b.y + 13} width={8} height={8} rx={1.5} style={{ fill: color }} />
      <text x={cx + 6} y={b.y + 21} text-anchor="middle" class="fd-node-tag">
        {tag}
      </text>
      <text x={cx} y={b.y + 42} text-anchor="middle" class="fd-node-label">
        {node.label}
      </text>
    </g>
  );
}

function EdgeLabel({ route, text }: { route: Route; text: string }) {
  const [x, y] = route.anchor;
  if (route.horizontal) {
    const dy = route.side > 0 ? 25 : -16;
    return (
      <text x={x} y={y + dy} text-anchor="middle" class="fd-edge-label">
        {text}
      </text>
    );
  }
  const dx = route.side < 0 ? -17 : 17;
  return (
    <text x={x + dx} y={y + 4} text-anchor={route.side < 0 ? 'end' : 'start'} class="fd-edge-label">
      {text}
    </text>
  );
}

// ── Edge routing ────────────────────────────────────────────────────────────
// Every edge is an orthogonal polyline. Nodes that share a row (or column)
// get a straight line; otherwise an L-shaped elbow, or a Z-shaped one, is
// chosen so the path does not run through another node. `via` waypoints from
// the data override all of that for edges that go round the drawing.

type Pt = [number, number];
type Box = { x: number; y: number; w: number; h: number };
export type Route = {
  points: Pt[];
  /** Midpoint of the longest segment: where the label and step badge sit. */
  anchor: Pt;
  horizontal: boolean;
  /** -1: label above/left of the line, 1: below/right (reverse of a pair). */
  side: -1 | 1;
};

const PAIR_GAP = 9;

function box(n: DNode): Box {
  return { x: n.x, y: n.y, w: n.w ?? DEFAULT_NODE_W, h: n.h ?? DEFAULT_NODE_H };
}

function overlap(a0: number, a1: number, b0: number, b1: number): [number, number] | null {
  const lo = Math.max(a0, b0);
  const hi = Math.min(a1, b1);
  return lo < hi ? [lo, hi] : null;
}

function within(v: number, lo: number, hi: number) {
  return v >= lo && v <= hi;
}

/** Point on the side of `b` that faces `p`. */
function portToward(b: Box, p: Pt): Pt {
  if (within(p[0], b.x, b.x + b.w)) return [p[0], p[1] < b.y ? b.y : b.y + b.h];
  if (within(p[1], b.y, b.y + b.h)) return [p[0] < b.x ? b.x : b.x + b.w, p[1]];
  return [b.x + b.w / 2, p[1] < b.y ? b.y : b.y + b.h];
}

function hitsBox(points: Pt[], b: Box): boolean {
  for (let i = 1; i < points.length; i++) {
    const [ax, ay] = points[i - 1];
    const [bx, by] = points[i];
    const inX = Math.max(ax, bx) > b.x && Math.min(ax, bx) < b.x + b.w;
    const inY = Math.max(ay, by) > b.y && Math.min(ay, by) < b.y + b.h;
    if (inX && inY) return true;
  }
  return false;
}

function straightOrElbow(s: Box, t: Box, others: Box[], offset: number): Pt[] {
  const scx = s.x + s.w / 2;
  const scy = s.y + s.h / 2;
  const tcx = t.x + t.w / 2;
  const tcy = t.y + t.h / 2;

  const oy = overlap(s.y, s.y + s.h, t.y, t.y + t.h);
  if (oy) {
    const y = (oy[0] + oy[1]) / 2 + offset;
    return tcx > scx ? [[s.x + s.w, y], [t.x, y]] : [[s.x, y], [t.x + t.w, y]];
  }
  const ox = overlap(s.x, s.x + s.w, t.x, t.x + t.w);
  if (ox) {
    const pick = within(scx, ox[0], ox[1]) ? scx : within(tcx, ox[0], ox[1]) ? tcx : (ox[0] + ox[1]) / 2;
    const x = pick + offset;
    return tcy > scy ? [[x, s.y + s.h], [x, t.y]] : [[x, s.y], [x, t.y + t.h]];
  }

  const down = tcy > scy;
  const right = tcx > scx;
  const sV: Pt = [scx, down ? s.y + s.h : s.y]; // source top/bottom
  const sH: Pt = [right ? s.x + s.w : s.x, scy]; // source left/right
  const tV: Pt = [tcx, down ? t.y : t.y + t.h]; // target top/bottom
  const tH: Pt = [right ? t.x : t.x + t.w, tcy]; // target left/right
  const laneY = down ? sV[1] + (tV[1] - sV[1]) * 0.3 : sV[1] - (sV[1] - tV[1]) * 0.3;
  const candidates: Pt[][] = [
    [sV, [scx, tcy], tH], // down/up, then across
    [sH, [tcx, scy], tV], // across, then down/up
    [sV, [scx, laneY], [tcx, laneY], tV], // Z through a lane between the rows
  ];
  return candidates.find((c) => !others.some((o) => hitsBox(c, o))) ?? candidates[0];
}

function anchorOf(points: Pt[]): { anchor: Pt; horizontal: boolean } {
  let best = 0;
  let len = -1;
  for (let i = 1; i < points.length; i++) {
    const l = Math.abs(points[i][0] - points[i - 1][0]) + Math.abs(points[i][1] - points[i - 1][1]);
    if (l > len) {
      len = l;
      best = i;
    }
  }
  const [a, b] = [points[best - 1], points[best]];
  return { anchor: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], horizontal: a[1] === b[1] };
}

export function routeEdges(d: Diagram): Record<string, Route> {
  const boxes = new Map(d.nodes.map((n) => [n.id, box(n)]));
  const out: Record<string, Route> = {};
  d.edges.forEach((e: DEdge, i) => {
    const s = boxes.get(e.from)!;
    const t = boxes.get(e.to)!;
    // Two edges between the same pair in opposite directions run side by side.
    const twin = d.edges.findIndex((f) => f.from === e.to && f.to === e.from);
    const side: -1 | 1 = twin !== -1 && twin < i ? 1 : -1;
    const offset = twin === -1 ? 0 : side * PAIR_GAP;
    let points: Pt[];
    if (e.via?.length) {
      points = [portToward(s, e.via[0]), ...e.via, portToward(t, e.via[e.via.length - 1])];
    } else {
      const others = d.nodes.filter((n) => n.id !== e.from && n.id !== e.to).map(box);
      points = straightOrElbow(s, t, others, offset);
    }
    out[e.id] = { points, side, ...anchorOf(points) };
  });
  return out;
}

/** A step's badge sits on its first highlighted edge, or else above its first node. */
function badgeAt(d: Diagram, routes: Record<string, Route>, highlight: string[]): Pt | null {
  const edge = highlight.find((id) => routes[id]);
  if (edge) return routes[edge].anchor;
  const node = d.nodes.find((n) => highlight.includes(n.id));
  return node ? [node.x + 12, node.y - 14] : null;
}

const css = `
.flowdiagram {
  margin: 32px 0;
  max-width: none;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}
@media (min-width: 1100px) {
  .flowdiagram { width: max(100%, min(var(--wide-w), calc(100vw - 400px))); }
}
.fd-head {
  display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 8px 24px;
}
.fd-title {
  font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.fd-legend {
  display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0; padding: 0; list-style: none;
  font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted);
}
.fd-legend li { display: inline-flex; align-items: center; gap: 6px; }
.fd-swatch { width: 10px; height: 10px; border-radius: 2px; }

.fd-canvas {
  overflow-x: auto;
  background-color: var(--surface);
  background-image: radial-gradient(var(--line) 0.8px, transparent 0.8px);
  background-size: 20px 20px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
.fd-canvas:focus-visible, .fd-code pre:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.fd-canvas svg { display: block; width: 100%; height: auto; }

.fd-edge path { stroke: var(--muted); stroke-width: 1.5; opacity: 0.55; }
.fd-edge.is-on path { stroke: var(--ink); stroke-width: 2.25; opacity: 1; }
.fd-edge.is-loop path { stroke-dasharray: 7 5; animation: fd-dash 1.1s linear infinite; }
.fd-head-dim { fill: var(--muted); }
.fd-head-on { fill: var(--ink); }
@keyframes fd-dash { to { stroke-dashoffset: -24; } }
@media (prefers-reduced-motion: reduce) { .fd-edge.is-loop path { animation: none; } }
.fd-edge-label {
  font-family: var(--font-mono); font-size: 12px; fill: var(--muted);
  paint-order: stroke; stroke: var(--surface); stroke-width: 4px; stroke-linejoin: round;
}
.fd-edge.is-on .fd-edge-label { fill: var(--ink); font-weight: 600; }

.fd-node-box { fill: var(--surface); stroke-width: 1.5; stroke-opacity: 0.4; transition: stroke-opacity 160ms ease; }
.fd-node.is-on .fd-node-box { stroke-width: 2.5; stroke-opacity: 1; }
.fd-node-tint { fill-opacity: 0; stroke: none; }
.fd-node.is-on .fd-node-tint { fill-opacity: 0.12; }
.fd-node-tag {
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 0.06em; fill: var(--muted);
}
.fd-node-label { font-family: var(--font-body); font-size: 15px; font-weight: 500; fill: var(--muted); }
.fd-node.is-on .fd-node-label { fill: var(--ink); }

.fd-badge circle { fill: var(--surface); stroke: var(--muted); stroke-width: 1.25; }
.fd-badge text { font-family: var(--font-mono); font-size: 12px; font-weight: 600; fill: var(--muted); }
.fd-badge.is-done circle { stroke: var(--ink); }
.fd-badge.is-done text { fill: var(--ink); }
.fd-badge.is-current circle { fill: var(--accent); stroke: var(--accent); }
.fd-badge.is-current text { fill: var(--accent-ink); }

.fd-controls { display: flex; flex-wrap: wrap; gap: 8px; }
.fd-btn {
  font: inherit; font-size: var(--text-small); font-weight: 500; padding: 6px 14px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.fd-btn-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.fd-btn:disabled { color: var(--muted); background: transparent; border-color: var(--line); cursor: default; }
.fd-btn:focus-visible, .fd-steps button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.fd-body { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px 32px; }
@media (min-width: 1000px) { .fd-body { grid-template-columns: minmax(0, 1fr) minmax(0, 20rem); } }
.fd-now { min-width: 0; display: grid; gap: 12px; align-content: start; justify-items: start; }
.fd-status {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.fd-caption { margin: 4px 0 0; max-width: 60ch; font-size: var(--text-body); line-height: 1.6; color: var(--ink); }
.fd-code { width: 100%; min-width: 0; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
.fd-code-label {
  margin: 0; padding: 8px 12px; border-bottom: 1px solid var(--line); background: var(--surface-2);
  font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted);
}
.fd-code pre {
  margin: 0; padding: 12px 16px; max-height: 24rem; overflow: auto;
  font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.55; color: var(--ink); background: var(--surface);
}

.fd-steps { margin: 0; padding: 0; list-style: none; display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: 2px; min-width: 0; }
.fd-steps button {
  display: flex; align-items: baseline; gap: 10px; width: 100%; padding: 6px 8px; text-align: left;
  font: inherit; font-size: var(--text-small); color: var(--muted); background: transparent;
  border: 0; border-left: 2px solid transparent; border-radius: 0; cursor: pointer;
}
.fd-steps button:hover { color: var(--ink); background: var(--surface-2); }
.fd-steps button[aria-current='step'] { color: var(--ink); background: var(--surface-2); border-left-color: var(--accent); }
.fd-steps-n { flex: none; min-width: 2ch; font-family: var(--font-mono); font-size: var(--text-label); font-weight: 600; }
.fd-steps-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`;
