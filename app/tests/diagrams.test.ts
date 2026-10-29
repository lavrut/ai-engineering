import { describe, expect, it } from 'vitest';
import { codeProblems, resolveCode } from '../src/lib/codeSourceText';
import { codeKey, validateDiagram, type Diagram } from '../src/lib/diagram';
import { evalPipeline } from '../src/data/diagrams/eval-pipeline';
import { inspectPipeline } from '../src/data/diagrams/inspect-pipeline';
import { harnessFlow } from '../src/data/diagrams/harness-flow';
import { mcpArchitecture } from '../src/data/diagrams/mcp-architecture';
import { mcpClient } from '../src/data/diagrams/mcp-client';
import { mcpHttp } from '../src/data/diagrams/mcp-http';
import { mcpOauth } from '../src/data/diagrams/mcp-oauth';
import { mlLoop } from '../src/data/diagrams/ml-loop';
import { mlPipeline } from '../src/data/diagrams/ml-pipeline';
import { requestResponse } from '../src/data/diagrams/request-response';
import { toolRoundtrip } from '../src/data/diagrams/tool-roundtrip';
import { agentPatterns } from '../src/data/diagrams/agent-patterns';
import { replayProxy } from '../src/data/diagrams/replay-proxy';
import { dataFabric } from '../src/data/diagrams/data-fabric';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const all: [string, Diagram][] = [
  ['harness-flow', harnessFlow],
  ['request-response', requestResponse],
  ['tool-roundtrip', toolRoundtrip],
  ['eval-pipeline', evalPipeline],
  ['inspect-pipeline', inspectPipeline],
  ['ml-pipeline', mlPipeline],
  ['ml-loop', mlLoop],
  ['mcp-architecture', mcpArchitecture],
  ['mcp-client', mcpClient],
  ['mcp-http', mcpHttp],
  ['mcp-oauth', mcpOauth],
  ['agent-patterns', agentPatterns],
  ['replay-proxy', replayProxy],
  ['data-fabric', dataFabric],
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

  // The MCP diagrams: each step links to a real section, and its caption ends by naming that same
  // section ("In the code: section N."), so a renumbered file breaks the build, not the lesson.
  describe('MCP diagrams', () => {
    const mcp: [string, Diagram, Record<number, [string, number]>][] = [
      ['mcp-architecture', mcpArchitecture, { 1: ['mcp-agent-host', 3], 2: ['mcp-client-connect', 1], 3: ['mcp-read', 2], 4: ['mcp-read', 3], 5: ['mcp-ops', 2], 6: ['mcp-ops', 4] }],
      ['mcp-client', mcpClient, { 1: ['mcp-client', 1], 2: ['mcp-client-connect', 1], 3: ['mcp-client', 2], 4: ['mcp-client-calls', 1], 5: ['mcp-client-calls', 2] }],
      ['mcp-http', mcpHttp, { 1: ['mcp-read', 5], 2: ['mcp-read', 5], 3: ['mcp-auth', 2], 4: ['mcp-auth', 3], 5: ['mcp-read', 5] }],
      ['mcp-oauth', mcpOauth, { 1: ['mcp-auth', 3], 2: ['mcp-auth-server', 4], 3: ['mcp-auth-server', 3], 4: ['mcp-auth-server', 2], 5: ['mcp-auth', 1], 6: ['mcp-auth', 2] }],
    ];

    it.each(mcp)('%s: every step links to the pinned source and section', (_name, d, want) => {
      expect(d.steps).toHaveLength(Object.keys(want).length);
      const got = Object.fromEntries(d.steps.map((s) => [s.n, [s.code?.source, s.code?.section]]));
      expect(got).toEqual(want);
    });

    it.each(mcp)('%s: every caption ends with "In the code: section N." matching its link', (_name, d) => {
      for (const s of d.steps) {
        const m = /In the code: section (\d+)\.$/.exec(s.caption);
        expect(m, `step ${s.n} caption`).not.toBeNull();
        expect(Number(m![1]), `step ${s.n}`).toBe(s.code!.section);
      }
    });

    it.each(mcp)('%s: only known role tokens, and 5 or 6 steps', (_name, d) => {
      const roles = new Set(['user', 'harness', 'model', 'tool', 'memory', 'logs', 'dataset', 'grader']);
      for (const n of d.nodes) expect(roles.has(n.role), n.id).toBe(true);
      expect(d.steps.length).toBeGreaterThanOrEqual(5);
      expect(d.steps.length).toBeLessThanOrEqual(6);
    });

    it('mcp-architecture shows the model, agent, client, both servers and what they touch', () => {
      expect(mcpArchitecture.nodes.map((n) => n.id)).toEqual(['model', 'agent', 'client', 'read', 'tickets', 'ops', 'labels']);
      expect(mcpArchitecture.nodes.find((n) => n.id === 'read')?.label).toBe('lumen-read');
      expect(mcpArchitecture.nodes.find((n) => n.id === 'ops')?.label).toBe('lumen-ops');
    });

    it('mcp-http names the explicit Host and Origin lists, the 131,072-byte cap and the one order that differs', () => {
      expect(mcpHttp.steps[0].caption).toContain('127.0.0.1:port or localhost:port');
      expect(mcpHttp.steps[0].caption).toContain('before any token is read');
      expect(mcpHttp.steps[0].caption).not.toMatch(/stateless/);
      expect(mcpHttp.steps[1].caption).toContain('131,072 bytes');
      expect(mcpHttp.steps[4].caption).toContain('413 in Go and 401 in Python');
    });

    it('mcp-oauth uses the pre-registered client as the main path, and warns that the test server is not for real users', () => {
      expect(mcpOauth.steps[1].caption).toContain('pre-registered public client lumen-cli is the main path');
      expect(mcpOauth.steps[2].caption).toContain('PKCE (S256 only)');
      expect(mcpOauth.steps[2].caption).toContain('127.0.0.1 and nothing else');
    });
  });

  // The AI Agents diagrams: each linked step ends its caption with "In the code: section N.",
  // pinned against its link, so a renumbered file breaks the build, not the lesson.
  describe('agents diagrams', () => {
    const agents: [string, Diagram, Record<number, [string, number] | null>][] = [
      [
        'agent-patterns',
        agentPatterns,
        { 1: null, 2: ['agents-patterns', 2], 3: ['agents-patterns', 3], 4: ['agents-patterns', 4], 5: ['agents-patterns', 5], 6: ['agents-patterns', 6], 7: ['agents-supervisor', 2] },
      ],
      [
        'replay-proxy',
        replayProxy,
        { 1: ['agents-proxy', 1], 2: ['agents-proxy', 2], 3: ['agents-proxy', 4], 4: ['agents-proxy-handler', 2], 5: ['agents-proxy-cost', 1], 6: ['agents-proxy-handler', 2], 7: ['agents-proxy', 3] },
      ],
    ];

    it.each(agents)('%s: every step links to the pinned source and section', (_name, d, want) => {
      expect(d.steps).toHaveLength(Object.keys(want).length);
      const got = Object.fromEntries(d.steps.map((s) => [s.n, s.code ? [s.code.source, s.code.section] : null]));
      expect(got).toEqual(want);
    });

    it.each(agents)('%s: every linked caption ends with "In the code: section N." matching its link', (_name, d) => {
      for (const s of d.steps) {
        const m = /In the code: section (\d+)\.$/.exec(s.caption);
        if (!s.code) {
          expect(m, `step ${s.n} has no link, so no section`).toBeNull();
          continue;
        }
        expect(m, `step ${s.n} caption`).not.toBeNull();
        expect(Number(m![1]), `step ${s.n}`).toBe(s.code.section);
      }
    });

    // Each link is pinned to what its section holds, not only to its number, so a caption about the
    // y/N prompt cannot keep pointing at section 4 after the prompt moves.
    it.each([
      ['agent-patterns', agentPatterns, { 2: 'def chain(', 3: 'def route(', 4: 'def parallel(', 5: 'def orchestrate(', 6: 'def evaluate_optimize(', 7: 'def single_agent(' }],
      [
        'replay-proxy',
        replayProxy,
        { 1: 'HOST = "127.0.0.1"', 2: 'def request_key(', 3: 'def playback_bytes(', 4: 'replay_miss', 5: 'def confirm(', 6: 'would pass --max-cost', 7: 'latest wins' },
      ],
    ] as const)('%s: each linked section holds what its caption describes', (_name, d, want) => {
      const got = Object.fromEntries(d.steps.filter((s) => s.code).map((s) => [s.n, resolveCode(s.code!).python]));
      expect(Object.keys(got).map(Number)).toEqual(Object.keys(want).map(Number));
      for (const [n, text] of Object.entries(want)) expect(got[Number(n)], `step ${n}`).toContain(text);
    });

    it('agent-patterns shows the five patterns as workflows, beside the agent loop', () => {
      expect(agentPatterns.nodes.map((n) => n.label)).toEqual([
        'Ticket', 'Workflow: code decides', 'Prompt chaining', 'Routing', 'Parallelization', 'Orchestrator-workers', 'Evaluator-optimizer',
        'Claude (model)', 'Agent: model decides', 'Tools',
      ]);
      expect(agentPatterns.steps[0].caption).toContain('Building effective agents');
      expect(agentPatterns.steps[6].caption).toContain('the model decides');
    });

    it('replay-proxy keeps the API behind record mode, the key, the cap and the y/N question', () => {
      expect(replayProxy.nodes.map((n) => n.id)).toEqual(['engine', 'proxy', 'gate', 'api', 'store']);
      expect(replayProxy.nodes.find((n) => n.id === 'proxy')?.label).toBe('Proxy on 127.0.0.1');
      expect(replayProxy.nodes.find((n) => n.id === 'api')?.label).toBe('api.anthropic.com');
      expect(replayProxy.edges.find((e) => e.to === 'gate')?.label).toBe('record mode only');
      expect(replayProxy.steps[0].caption).toContain('ANTHROPIC_BASE_URL to http://127.0.0.1:8790');
      expect(replayProxy.steps[3].caption).toContain('404');
      expect(replayProxy.steps[3].caption).toContain('never forwarded');
      expect(replayProxy.steps[4].caption).toContain('y/N');
      expect(replayProxy.steps[5].caption).toContain('--max-cost');
      expect(replayProxy.steps[5].caption).toContain('from the environment');
      expect(replayProxy.steps[6].caption).toContain('latest wins');
    });
  });

  // The Data Fabric diagram is drawn from lineage.json (data/python/catalog.py writes it): every dataset is a
  // node, every track that writes one is a producer on the left, every track that reads one a consumer on the
  // right (a lineage consumer such as mcp-read-server folds into its track), and a contract rides on each edge
  // that has one. The test rebuilds the edges from lineage.json, so a new dataset or consumer fails here.
  describe('data-fabric', () => {
    type LNode = { id: string; kind: 'track' | 'consumer' | 'dataset'; track?: string };
    type LEdge = { from: string; to: string; contract: string | null; version: string | null };
    const here = dirname(fileURLToPath(import.meta.url));
    const lineage = JSON.parse(readFileSync(join(here, '../../data/data/derived/lineage.json'), 'utf8')) as { nodes: LNode[]; edges: LEdge[] };
    const catalog = JSON.parse(readFileSync(join(here, '../../data/data/derived/catalog.json'), 'utf8')) as Record<string, unknown>;
    const kind = new Map(lineage.nodes.map((n) => [n.id, n]));
    const id = (n: string, side: 'from' | 'to') => {
      const node = kind.get(n)!;
      if (node.kind === 'dataset') return n;
      if (node.kind === 'track') return `p-${n}`;
      return side === 'to' ? `c-${node.track}` : n;
    };
    const roleOf = (n: string) => dataFabric.nodes.find((x) => x.id === n)?.role;

    it('has every catalogued dataset as a dataset node (the eleven plus tickets_v2)', () => {
      const datasets = dataFabric.nodes.filter((n) => n.role === 'dataset').map((n) => n.id);
      expect([...datasets].sort()).toEqual(Object.keys(catalog).sort());
      expect(datasets).toHaveLength(12);
      for (const n of dataFabric.nodes.filter((n) => n.role === 'dataset')) expect(n.label).toBe(n.id);
    });

    it('draws producers on the left and consumers on the right, one node per track', () => {
      expect(dataFabric.nodes.filter((n) => n.id.startsWith('p-')).map((n) => n.id)).toEqual(['p-llms', 'p-data', 'p-ml', 'p-mcp', 'p-agents']);
      expect(dataFabric.nodes.filter((n) => n.id.startsWith('c-')).map((n) => n.id)).toEqual(['c-mcp', 'c-agents', 'c-llms', 'c-ml', 'c-app']);
      for (const n of dataFabric.nodes.filter((n) => n.id.startsWith('p-'))) expect(roleOf(n.id)).toBe('tool');
      for (const n of dataFabric.nodes.filter((n) => n.id.startsWith('c-'))) expect(roleOf(n.id)).toBe('user');
      const xs = (p: string) => new Set(dataFabric.nodes.filter((n) => n.id.startsWith(p)).map((n) => n.x));
      const dx = new Set(dataFabric.nodes.filter((n) => n.role === 'dataset').map((n) => n.x));
      expect([xs('p-').size, dx.size, xs('c-').size]).toEqual([1, 1, 1]);
      expect([...xs('p-')][0]).toBeLessThan([...dx][0]);
      expect([...dx][0]).toBeLessThan([...xs('c-')][0]);
    });

    it("has exactly lineage.json's edges, folded to tracks, each carrying its contract", () => {
      const want = new Set(lineage.edges.map((e) => `${id(e.from, 'from')} -> ${id(e.to, 'to')} [${e.contract ?? ''}]`));
      const got = dataFabric.edges.map((e) => `${e.from} -> ${e.to} [${/^([a-z-]+\.v\d+)/.exec(e.label ?? '')?.[1] ?? ''}]`);
      expect(new Set(got).size).toBe(got.length);
      expect(new Set(got)).toEqual(want);
      expect(got).toHaveLength(31);
      for (const e of dataFabric.edges) expect(e.id).toBe(`e-${e.from}-${e.to}`);
      // Only an edge with a contract is labelled, and the migration edge says what it is.
      expect(dataFabric.edges.filter((e) => e.label).map((e) => e.label)).toEqual([
        'tickets.v1', 'tickets.v2', 'complaints.v1', 'agent-traces.v1', 'tickets.v1, migrated', 'tickets.v1', 'tickets.v2', 'complaints.v1', 'agent-traces.v1',
      ]);
    });

    it('links its steps to the pinned sources and sections, each caption ending with its section', () => {
      const got = Object.fromEntries(dataFabric.steps.map((s) => [s.n, s.code ? [s.code.source, s.code.section, s.code.goSection ?? null] : null]));
      expect(got).toEqual({
        1: null,
        2: ['data-catalog', 3, null],
        3: ['data-contract-tickets', 5, null],
        4: ['data-quality', 4, null],
        5: ['data-contract', 3, 7],
        6: ['data-gate', 2, null],
      });
      for (const s of dataFabric.steps) {
        const m = /In the code: section (\d+)\.$/.exec(s.caption);
        if (!s.code) {
          expect(m, `step ${s.n}`).toBeNull();
          continue;
        }
        expect(Number(m![1]), `step ${s.n}`).toBe(s.code.section);
      }
    });

    it('links each step to the section that holds what its caption describes', () => {
      const want: Record<number, [string, string?]> = {
        2: ['def catalog('],
        3: ['- name: mcp-read-server'],
        4: ['def build(as_of: date) -> dict:'],
        5: ['def _field_rules(', 'func fieldRules('],
        6: ['def gate('],
      };
      for (const [n, [py, go]] of Object.entries(want)) {
        const step = dataFabric.steps[Number(n) - 1];
        const code = resolveCode(step.code!);
        expect(code.python, `step ${n}`).toContain(py);
        if (go) expect(code.go, `step ${n}`).toContain(go);
      }
    });

    it('names the numbers it states as lineage.json and gate.json have them', () => {
      expect(dataFabric.steps[0].caption).toContain('12 datasets');
      expect(dataFabric.steps[0].caption).toContain('5 tracks');
      expect(dataFabric.steps[5].caption).toContain('2027-01-31');
      expect(dataFabric.steps[5].caption).toContain('priority');
      expect(dataFabric.steps[5].caption).toContain('urgency');
    });
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
