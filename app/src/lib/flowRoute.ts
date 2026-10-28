// Edge routing for FlowDiagram: pure geometry, no DOM. The component draws what routeEdges returns.
import { DEFAULT_NODE_H, DEFAULT_NODE_W, type DEdge, type DNode, type Diagram } from './diagram';

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

export function box(n: DNode): Box {
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
export function badgeAt(d: Diagram, routes: Record<string, Route>, highlight: string[]): Pt | null {
  const edge = highlight.find((id) => routes[id]);
  if (edge) return routes[edge].anchor;
  const node = d.nodes.find((n) => highlight.includes(n.id));
  return node ? [node.x + 12, node.y - 14] : null;
}
