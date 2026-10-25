// Data model for the step-through FlowDiagram. Pure data plus a validator.
//
// Layout conventions (shared by every diagram so the SVG renderer is simple):
// - x/y are the node's top-left corner in SVG user units.
// - w/h default to DEFAULT_NODE_W x DEFAULT_NODE_H when omitted.
// - Flow reads left-to-right, then top-to-bottom; a diagram may "snake" back
//   right-to-left on a lower row. The Trace log sits as a tall side rail.
// - Edges are drawn as orthogonal (elbow) paths chosen by the renderer. An
//   edge that must go around the drawing lists `via` waypoints; the path runs
//   from the source's side facing the first waypoint, through each waypoint,
//   into the target's side facing the last one. Keep waypoints axis-aligned.

export type Role = 'user' | 'harness' | 'model' | 'tool' | 'memory' | 'logs';
export type DNode = { id: string; label: string; role: Role; x: number; y: number; w?: number; h?: number };
export type DEdge = {
  id: string;
  from: string;
  to: string;
  label?: string;
  loop?: boolean;
  via?: [number, number][];
};
export type DStep = {
  n: number;
  highlight: string[];
  caption: string;
  code?: { file: 'python' | 'go'; section: number };
};
export type Diagram = {
  id: string;
  title: string;
  width: number;
  height: number;
  nodes: DNode[];
  edges: DEdge[];
  steps: DStep[];
};

export const DEFAULT_NODE_W = 150;
export const DEFAULT_NODE_H = 56;

/** Returns a list of human-readable problems; an empty list means valid. */
export function validateDiagram(d: Diagram): string[] {
  const errors: string[] = [];
  const ids = new Set<string>();
  const dupes = new Set<string>();
  for (const id of [...d.nodes.map((n) => n.id), ...d.edges.map((e) => e.id)]) {
    if (ids.has(id)) dupes.add(id);
    ids.add(id);
  }
  for (const id of dupes) errors.push(`duplicate id: ${id}`);

  const nodeIds = new Set(d.nodes.map((n) => n.id));
  for (const e of d.edges) {
    if (!nodeIds.has(e.from)) errors.push(`edge ${e.id}: unknown from node ${e.from}`);
    if (!nodeIds.has(e.to)) errors.push(`edge ${e.id}: unknown to node ${e.to}`);
  }

  for (const n of d.nodes) {
    const w = n.w ?? DEFAULT_NODE_W;
    const h = n.h ?? DEFAULT_NODE_H;
    if (n.x < 0 || n.y < 0 || n.x + w > d.width || n.y + h > d.height) {
      errors.push(`node ${n.id} is outside ${d.width}x${d.height}`);
    }
  }

  d.steps.forEach((s, i) => {
    if (s.n !== i + 1) errors.push(`step ${i + 1} is numbered ${s.n}; steps must run 1..${d.steps.length}`);
    for (const h of s.highlight) {
      if (!ids.has(h)) errors.push(`step ${s.n}: highlight ${h} is not a node or edge id`);
    }
    if (s.code && !(Number.isInteger(s.code.section) && s.code.section >= 1 && s.code.section <= 9)) {
      errors.push(`step ${s.n}: code section ${s.code.section} not in 1..9`);
    }
  });

  return errors;
}
