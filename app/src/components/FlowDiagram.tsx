import { useEffect, useId, useState } from 'preact/hooks';
import { codeKey, type CodeSource, type DNode, type Diagram, type Role } from '../lib/diagram';
import { fileName } from '../lib/codeSources';
import { badgeAt, box, routeEdges, type Route } from '../lib/flowRoute';
import { LANG_EVENT, pickLang, readLang, type Lang } from '../lib/lang';

// The edge routing is pure logic in lib/flowRoute.ts; re-exported for the tests that import it from here.
export { routeEdges, type Route } from '../lib/flowRoute';

type Props = {
  diagram: Diagram;
  /** Source sections keyed by codeKey ("6" for the harness, "llms:6" for the lab, "ml-serve:4:go5" with a goSection; only those the steps use). */
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
  const source: CodeSource = step.code?.source ?? 'harness';
  const key = step.code ? codeKey(step.code) : undefined;
  // A Python-only source (or a missing Go section) shows the Python code even when Go is chosen.
  const codeLang: Lang = lang === 'go' && key !== undefined && codeGo[key] === undefined ? 'python' : lang;
  const code = key === undefined ? undefined : (codeLang === 'go' ? codeGo : codePython)[key];
  // The Go file may number its own sections (goSection); the label shows the one on screen.
  const section = codeLang === 'go' ? (step.code?.goSection ?? step.code?.section) : step.code?.section;
  const uid = useId(); // the course is one page, and a diagram can appear twice on it
  const codeId = `${diagram.id}-${uid}-code`;
  const arrow = `${diagram.id}-${uid}-arrow`;

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
              <p class="fd-code-label">{`${fileName(source, codeLang)} · section ${section}`}</p>
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
