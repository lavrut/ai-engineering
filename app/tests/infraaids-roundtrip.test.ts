// Round trips for the AI Infrastructure aids' props: the aids reshape infra/data/derived/*.json (replay.py) into
// what a lesson shows, but never rewrite it. Every record they ship is taken back to the record it came from and
// compared field by field, so a reshaping that drops, renames or rounds a value fails here.
// (a) RouteTable: one row per configured route, in the file's order, plus the '*' row; each rebuilds its route.
// (b) RetryTimeline: one timeline per injector script, rebuilding capstone.json's injector entry.
// (c) CacheReport: all, each set and each team rebuild their figures; the about text is verbatim.
// (d) LedgerView: the teams rebuild ledger.json's teams, the lines are the config's budgets, the top tags and the
//     agents figures are ledger.json's; the gateway's per-set cost sits beside the agents ledger.
// (e) TraceExplorer: the three labelled traces are the sample's, unchanged; the gate lines are verbatim.
// Each builder's props also serialise, as Astro ships them, under the per-aid budget; an unknown pick throws
// naming the known ones.
import { describe, expect, it } from 'vitest';
// Astro's own prop serialiser and escaper: the budget measures the props as the built page carries them.
import { serializeProps } from 'astro/runtime/server/serialize.js';
import { escapeHTML } from 'astro/runtime/server/escape.js';
import { loadInfraData } from '../src/lib/infraData';
import {
  ALL,
  DENY_ROW,
  REPLAY_KEY_NOTE,
  cacheReportProps,
  filterTraces,
  ledgerViewProps,
  retryTimelineProps,
  routeFor,
  routeTableProps,
  teamState,
  traceExplorerProps,
  type FiguresJson,
  type GateLineJson,
  type InjectorScriptJson,
} from '../src/lib/infraAids';

const data = loadInfraData();
const cap = data.capstone;

describe('infra data', () => {
  it('loads the three derived files', () => {
    expect(cap.calls).toBe(957);
    expect(Object.keys(cap.sets)).toEqual(['desk-hand', 'desk-hand-go', 'desk-langgraph', 'desk-sdk', 'patterns', 'patterns-go', 'judge']);
    expect(Object.keys(cap.teams)).toEqual(['desk', 'patterns', 'judge']);
    expect(Object.keys(data.ledger.teams)).toEqual(['desk', 'patterns', 'judge']);
    expect(data.traces.traces.map((t) => t.label)).toEqual(['cached', 'retried', 'refused']);
    expect(cap.cases).toHaveLength(14);
  });
});

describe('RouteTable round trip', () => {
  const p = routeTableProps(cap.config);

  it("rebuilds the config's routes, in order, and ends with the '*' deny row", () => {
    const routed = p.rows.filter((r) => r.model !== DENY_ROW);
    expect(Object.fromEntries(routed.map((r) => [r.model, { to: r.to, fallback: r.fallback }]))).toEqual(cap.config.routes);
    expect(routed.map((r) => r.model)).toEqual(Object.keys(cap.config.routes));
    for (const r of routed) {
      expect(r.rule, r.model).toBe('route');
      expect({ rpm: r.rpm, tpm: r.tpm }, r.model).toEqual({ rpm: cap.config.models[r.to!].rpm, tpm: cap.config.models[r.to!].tpm });
    }
    expect(p.rows).toHaveLength(Object.keys(cap.config.routes).length + 1);
    expect(p.rows.at(-1)).toEqual({ model: DENY_ROW, rule: 'deny', to: null, fallback: [], rpm: null, tpm: null });
    expect(cap.config.deny_unknown).toBe(true);
    expect(p.models).toEqual(Object.keys(cap.config.models));
    expect(p.upstream).toBe(cap.config.upstream);
  });

  it('states that the replay key is over method, path and body, so routing never edits the body', () => {
    expect(p.replayKey).toBe(REPLAY_KEY_NOTE);
    expect(p.replayKey).toMatch(/method, path and body/);
    expect(p.replayKey).toMatch(/never edits the body/);
  });

  it('routes a configured model to its row and any other model to the deny row', () => {
    expect(routeFor(p, 'claude-haiku-5-5')).toBe(p.rows[0]);
    expect(routeFor(p, 'claude-opus-9')).toBe(p.rows.at(-1));
    expect(routeFor(p, 'claude-opus-9').rule).toBe('deny');
  });

  it('without deny_unknown, the * row sends any other model as itself', () => {
    const q = routeTableProps({ ...cap.config, deny_unknown: false });
    expect(q.rows.at(-1)).toMatchObject({ model: DENY_ROW, rule: 'route', to: null });
  });
});

describe('RetryTimeline round trip', () => {
  const p = retryTimelineProps(cap.injector);

  it("rebuilds capstone.json's injector entries, one timeline per script, in order", () => {
    expect(p.scripts.map((s) => s.id)).toEqual(Object.keys(cap.injector));
    expect(p.scripts).toHaveLength(3);
    const back = Object.fromEntries(
      p.scripts.map((s): [string, InjectorScriptJson] => [
        s.id,
        {
          script: s.script,
          status: s.status,
          outcome: s.outcome,
          decisions: s.decisions.map((d) => [d.stage, d.verdict]),
          attempts: s.attempts.map(({ model, status, backoff_ms }) => ({ model, status, backoff_ms })),
        },
      ]),
    );
    expect(back).toEqual(cap.injector);
    for (const s of p.scripts) expect(s.attempts.map((a) => a.n), s.id).toEqual(s.attempts.map((_, i) => i + 1));
    expect(p.initial).toBe('flaky');
  });

  it('opens on a named script, and an unknown one throws naming the known ones', () => {
    expect(retryTimelineProps(cap.injector, { initial: 'dead' }).initial).toBe('dead');
    expect(() => retryTimelineProps(cap.injector, { initial: 'nope' })).toThrow(/unknown script "nope".*flaky, dead, ratelimited/);
  });
});

describe('CacheReport round trip', () => {
  const p = cacheReportProps(cap);
  const back = (r: (typeof p.sets)[number]) => ({
    calls: r.calls,
    cache_hits: r.hits,
    hit_rate: r.hit_rate,
    prompt_cache_read_tokens: r.prompt_cache_read_tokens,
    prompt_cache_write_tokens: r.prompt_cache_write_tokens,
  });
  const pick = (f: FiguresJson) => ({
    calls: f.calls,
    cache_hits: f.cache_hits,
    hit_rate: f.hit_rate,
    prompt_cache_read_tokens: f.prompt_cache_read_tokens,
    prompt_cache_write_tokens: f.prompt_cache_write_tokens,
  });

  it('rebuilds all, each set and each team from the figures, in order', () => {
    expect(back(p.all)).toEqual(pick(cap.all));
    expect(p.sets.map((r) => r.id)).toEqual(Object.keys(cap.sets));
    expect(p.teams.map((r) => r.id)).toEqual(Object.keys(cap.teams));
    for (const r of p.sets) expect(back(r), r.id).toEqual(pick(cap.sets[r.id]));
    for (const r of p.teams) expect(back(r), r.id).toEqual(pick(cap.teams[r.id]));
    expect(p.sets.reduce((a, r) => a + r.calls, 0)).toBe(cap.all.calls);
    expect(p.sets.reduce((a, r) => a + r.hits, 0)).toBe(cap.all.cache_hits);
    expect(p.teams.reduce((a, r) => a + r.calls, 0)).toBe(cap.all.calls);
  });

  it("carries about.cache verbatim and the config's cache rules", () => {
    expect(p.about).toBe(cap.about.cache);
    expect({ ttl_s: p.ttl_s, skip_tools: p.skip_tools }).toEqual(cap.config.cache);
  });

  it('opens on all, a set or a team, and an unknown pick throws naming the known ones', () => {
    expect(p.initial).toBe(ALL);
    expect(cacheReportProps(cap, { initial: 'patterns-go' }).initial).toBe('patterns-go');
    expect(cacheReportProps(cap, { initial: 'desk' }).initial).toBe('desk');
    expect(() => cacheReportProps(cap, { initial: 'nope' })).toThrow(/unknown set or team "nope".*all, desk-hand, .*patterns-go, judge, desk, patterns/);
  });
});

describe('LedgerView round trip', () => {
  const p = ledgerViewProps(data.ledger, cap.config.budgets, { sets: cap.sets });

  it("rebuilds ledger.json's teams, with the config's limit and warn lines beside each", () => {
    expect(p.teams.map((t) => t.team)).toEqual(Object.keys(data.ledger.teams));
    expect(Object.fromEntries(p.teams.map(({ team, requests, cost_usd, cached, refused }) => [team, { requests, cost_usd, cached, refused }]))).toEqual(
      data.ledger.teams,
    );
    for (const t of p.teams) {
      const b = cap.config.budgets[t.team];
      expect({ limit_usd: t.limit_usd, warn_usd: t.warn_usd }, t.team).toEqual(b);
      expect(t.state, t.team).toBe(teamState(t.cost_usd, b));
    }
    expect(p.teams.map((t) => t.state)).toEqual(['under', 'under', 'under']);
    expect(p.idle).toEqual(Object.keys(cap.config.budgets).filter((k) => !(k in data.ledger.teams)));
    expect(p.idle).toEqual(['lab']);
    expect(p.teams.reduce((a, t) => a + t.requests, 0)).toBe(cap.calls);
  });

  it('names a state by the lines: under, warn past warn_usd, over past limit_usd', () => {
    const b = { limit_usd: 1, warn_usd: 0.8 };
    expect([teamState(0.5, b), teamState(0.9, b), teamState(1.1, b)]).toEqual(['under', 'warn', 'over']);
    expect(teamState(0.9, { limit_usd: 1, warn_usd: null })).toBe('under');
    expect(teamState(0.1, undefined)).toBe('over');
  });

  it("ships the top tags and the agents ledger as ledger.json has them, with the gateway's set cost beside", () => {
    expect(p.top_tags.map((t) => [t.tag, t.cost_usd])).toEqual(data.ledger.top_tags);
    expect(p.agents.map((a) => a.set)).toEqual(Object.keys(data.ledger.agents));
    for (const a of p.agents) {
      expect(a.agents_usd, a.set).toBe(data.ledger.agents[a.set]);
      expect(a.gateway_usd, a.set).toBe(cap.sets[a.set].cost_usd);
      expect(a.cache_hits, a.set).toBe(cap.sets[a.set].cache_hits);
      expect(a.desk, a.set).toBe(a.set.startsWith('desk-'));
    }
    expect(p.about).toEqual(data.ledger.about);
    // Without the capstone's sets the agents figures still ship, with nothing beside them.
    const bare = ledgerViewProps(data.ledger, cap.config.budgets);
    expect(bare.agents.every((a) => a.gateway_usd === null && a.cache_hits === null)).toBe(true);
  });

  it('opens on every team or one, and an unknown team throws naming the known ones', () => {
    expect(p.initial).toBe(ALL);
    expect(ledgerViewProps(data.ledger, cap.config.budgets, { initial: 'judge' }).initial).toBe('judge');
    expect(() => ledgerViewProps(data.ledger, cap.config.budgets, { initial: 'lab2' })).toThrow(/unknown team "lab2".*all, desk, patterns, judge/);
  });
});

describe('TraceExplorer round trip', () => {
  const p = traceExplorerProps(data.traces, cap.gate);

  it('ships the three labelled traces unchanged, with their decisions', () => {
    expect(p.traces).toEqual(data.traces.traces);
    expect(p.traces.map((t) => [t.label, t.run, t.trace.outcome])).toEqual([
      ['cached', 'capstone', 'cached'],
      ['retried', 'flaky', 'ok'],
      ['refused', 'demo', 'refused'],
    ]);
    expect(p.traces.map((t) => t.trace.decisions.map((d) => d.stage))).toEqual([
      ['tag', 'budget', 'cache', 'ledger', 'trace'],
      ['tag', 'budget', 'cache', 'route', 'limit', 'retry', 'ledger', 'trace'],
      ['tag', 'budget'],
    ]);
    expect(p.about).toEqual(data.traces.about);
  });

  it('lists the filter values the traces carry', () => {
    expect(p.teams).toEqual(['desk', 'patterns']);
    expect(p.tags).toEqual(['t001', 't007', 't008']);
    expect(p.outcomes).toEqual(['cached', 'ok', 'refused']);
    expect(filterTraces(p, { team: 'desk' }).map((t) => t.label)).toEqual(['cached', 'retried']);
    expect(filterTraces(p, { outcome: 'refused' }).map((t) => t.label)).toEqual(['refused']);
    expect(filterTraces(p, { team: 'desk', tag: 't008' }).map((t) => t.label)).toEqual(['retried']);
    expect(filterTraces(p, {}).map((t) => t.label)).toEqual(['cached', 'retried', 'refused']);
  });

  it('carries the gate verbatim', () => {
    expect(p.gate.ok).toBe(cap.gate.ok);
    expect(p.gate.lines.map((l): GateLineJson => [l.name, l.ok, l.detail])).toEqual(cap.gate.lines);
    expect(p.gate.lines.map((l) => l.name)).toEqual(['ledger', 'budgets', 'stages', 'figures']);
  });

  it('opens on a named trace, and an unknown one throws naming the known ones', () => {
    expect(p.initial).toBe('cached');
    expect(traceExplorerProps(data.traces, cap.gate, { initial: 'refused' }).initial).toBe('refused');
    expect(() => traceExplorerProps(data.traces, cap.gate, { initial: 'slow' })).toThrow(/unknown trace "slow".*cached, retried, refused/);
  });
});

// ── Props budget (spec: the track's props under 200 KB) ─────────────────────
// Each aid's props as the built page carries them, serialised and escaped by Astro's own code, under the per-aid
// budget; the five together under the track's 200 KB.
const shipped = (props: object) => Buffer.byteLength(escapeHTML(serializeProps(props, {} as never)), 'utf8');
const PER_AID = 30_000;

describe('props budget', () => {
  it(`keeps each aid's props under ${PER_AID} bytes and the five under 200 KB`, () => {
    const parts: [string, number][] = [
      ['RouteTable', shipped(routeTableProps(cap.config))],
      ['RetryTimeline', shipped(retryTimelineProps(cap.injector, { about: cap.about.injector }))],
      ['CacheReport', shipped(cacheReportProps(cap))],
      ['LedgerView', shipped(ledgerViewProps(data.ledger, cap.config.budgets, { sets: cap.sets }))],
      ['TraceExplorer', shipped(traceExplorerProps(data.traces, cap.gate))],
    ];
    const line = parts.map(([k, n]) => `${k} ${n}`).join(', ');
    console.info(`infra props: ${line}; total ${parts.reduce((a, [, n]) => a + n, 0)}`);
    for (const [k, n] of parts) {
      expect(n, k).toBeLessThan(PER_AID);
      expect(n, k).toBeGreaterThan(200);
    }
    expect(parts.reduce((a, [, n]) => a + n, 0), line).toBeLessThan(200_000);
  });
});
