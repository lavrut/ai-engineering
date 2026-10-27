// Pure string helpers; no Node APIs so this also runs at Astro build time.
// A marker may be indented: the MCP servers build their tools inside a function, so sections 2-4
// sit one level in (and gofmt keeps a Go comment at the code's indent).
const MARKER = /^[ \t]*(?:#|\/\/) ── (\d+)\.\s*(.*?)\s*─*\s*$/;
const ANY_MARKER = /^[ \t]*(?:#|\/\/) ── /;

export function listSections(source: string): { n: number; title: string }[] {
  const out: { n: number; title: string }[] = [];
  for (const line of source.split('\n')) {
    const m = MARKER.exec(line);
    if (m) out.push({ n: Number(m[1]), title: m[2] });
  }
  return out;
}

export function extractSection(source: string, n: number): string {
  const lines = source.split('\n');
  const start = lines.findIndex((l) => MARKER.exec(l)?.[1] === String(n));
  if (start === -1) throw new Error(`Section ${n} not found`);
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (ANY_MARKER.test(lines[i])) {
      end = i;
      break;
    }
  }
  while (end > start + 1 && lines[end - 1].trim() === '') end--;
  // An indented section is shown dedented by its marker's indent, so a lesson quotes it flush left.
  const indent = /^[ \t]*/.exec(lines[start])![0];
  return lines
    .slice(start, end)
    .map((l) => (indent && l.startsWith(indent) ? l.slice(indent.length) : l))
    .join('\n');
}
