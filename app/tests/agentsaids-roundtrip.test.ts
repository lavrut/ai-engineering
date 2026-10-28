// Round trips for the agents aids' compact props: every entry the aids ship is encoded as the props carry
// it, decoded with the island's own decoder, and compared with the derived JSON entry it was built from.
// (a) The tuple carries the original fields exactly: every number, string and flag, nothing rounded.
// (b) The decoded text is what that original entry should read, built here straight from its fields.
//
// Lossy by design, and asserted as such:
// - is_error null and false both encode as 0 (neither shows an error; patterns.json's 30 orchestrator
//   tool records have is_error null).
// - A handoff's `changed` keys are joined with ", " (split back exactly, since no key holds ", ").
// - Rounding happens only when the text is formatted (usd, ms, num), never in the tuple.
import { describe, expect, it } from 'vitest';
import { loadAgentsData } from '../src/lib/agentsData';
import {
  ENGINES,
  agentTraceProps,
  engineScorecardProps,
  ms,
  num,
  patternStepView,
  patternStepperProps,
  scoreChecks,
  ticketView,
  traceStepView,
  usd,
  type PatternRunJson,
  type ScoreRunJson,
  type StepJson,
  type TraceRunJson,
} from '../src/lib/agentsAids';

const data = loadAgentsData();
const yn = (x: boolean | null, none: string) => (x === null ? none : x ? 'yes' : 'no');
const model = (tin: number | null, tout: number | null, cache: number | null, cost: number | null, t: number | null) =>
  `${num(tin)} tokens in, ${num(tout)} out${cache ? `, ${num(cache)} cached` : ''} · ${usd(cost)} · ${ms(t)}`;
const call = (name: string, args: string | null) => (args === null ? `${name} (arguments not logged)` : `${name}(${args})`);
const bit = (x: boolean | null) => (x ? 1 : 0); // the lossy is_error encoding: null and false are both 0
const tri = (x: boolean | null) => (x === null ? null : x ? 1 : 0);

// ── AgentTrace: every step of every run, all four engines ───────────────────
describe('AgentTrace steps round-trip', () => {
  const props = agentTraceProps(data.traces);
  const runs: [string, TraceRunJson, (typeof props.runs)[string][string][string]][] = [];
  for (const [desk, byEngine] of Object.entries(data.traces.runs)) {
    for (const [engine, byTicket] of Object.entries(byEngine)) {
      for (const [ticket, r] of Object.entries(byTicket)) {
        if ('steps' in r) runs.push([`${desk}/${engine}/${ticket}`, r, props.runs[desk][engine][ticket]]);
      }
    }
  }

  it('covers every recorded run in traces.json', () => {
    expect(runs).toHaveLength(2 * ENGINES.length * data.traces.tickets.length);
    expect(runs.reduce((a, [, r]) => a + r.steps.length, 0)).toBe(438);
  });

  const expected = (s: StepJson) => {
    switch (s.kind) {
      case 'model':
        return { tuple: [0, s.agent, s.tokens_in, s.tokens_out, s.cache_read, s.cost_usd, s.ms], view: { kind: 'model', title: `${s.agent} calls the model`, detail: model(s.tokens_in, s.tokens_out, s.cache_read, s.cost_usd, s.ms) } };
      case 'tool': {
        const flag = s.write ?? (s.is_error ? 'error' : undefined);
        const code = s.write === 'refused' ? 1 : s.write === 'written' ? 2 : s.is_error ? 3 : 0;
        const view = { kind: 'tool', title: `${s.agent}: ${s.name}`, detail: call(s.name, s.args), ...(flag ? { flag } : {}), ...(s.traced === false ? { note: 'Not in the trace: placed by its order in the tool calls.' } : {}) };
        return { tuple: [1, s.agent, s.name, s.args, code, s.traced === false ? 1 : 0], view };
      }
      case 'handoff':
        return {
          tuple: [2, s.agent, s.worker, s.task, s.changed.join(', '), bit(s.is_error)],
          view: { kind: 'handoff', title: `${s.agent} hands off to ${s.worker}`, detail: `Task: ${s.task ?? 'not logged'} · Changed: ${s.changed.join(', ') || 'nothing'}`, flag: s.is_error ? 'error' : 'handoff' },
        };
      case 'stop':
        return { tuple: [3, s.agent, s.name], view: { kind: 'stop', title: `${s.agent} stopped: ${s.name}`, detail: `The run hit its limit (${s.name}).`, flag: 'stop' } };
      default:
        return { tuple: [4, s.agent, s.name, s.message ?? null], view: { kind: 'error', title: `${s.agent}: error ${s.name}`, detail: s.message ?? s.name, flag: 'error' } };
    }
  };

  it.each(runs)('%s: every step decodes to its traces.json record', (_name, json, shipped) => {
    if (!shipped.recorded) throw new Error('recorded run shipped as not recorded');
    expect(shipped.steps).toHaveLength(json.steps.length);
    json.steps.forEach((s, i) => {
      const want = expected(s);
      expect(shipped.steps[i], `step ${i + 1} tuple`).toEqual(want.tuple);
      expect(traceStepView(shipped.steps[i]), `step ${i + 1} text`).toEqual(want.view);
      if (s.kind === 'handoff') expect(String(shipped.steps[i][4]).split(', ').filter(Boolean)).toEqual(s.changed);
    });
  });

  it.each(runs)("%s: the scorer's row, totals and reply decode to the original", (_name, json, shipped) => {
    if (!shipped.recorded) throw new Error('recorded');
    const sc = json.score!;
    expect(shipped.score).toEqual([tri(sc.task_success), tri(sc.category_ok), tri(sc.judge_pass), tri(sc.trajectory_ok), tri(sc.safety_fail), tri(sc.unconfirmed_write)]);
    expect(scoreChecks(shipped.score!)).toEqual([
      { label: 'Task success', value: yn(sc.task_success, 'not scored') },
      { label: 'Category right', value: yn(sc.category_ok, 'not scored') },
      { label: 'Judge passed the reply', value: yn(sc.judge_pass, 'no judge recording') },
      { label: 'Trajectory as expected', value: yn(sc.trajectory_ok, 'not evaluable') },
      { label: 'Safety failure', value: yn(sc.safety_fail, 'not scored') },
      { label: 'Write without confirmation', value: yn(sc.unconfirmed_write, 'not scored') },
    ]);
    const t = json.totals;
    expect(shipped.totals).toEqual({
      calls: t.calls,
      cost: usd(t.cost_usd),
      ...(sc.sdk_cost_usd === null ? {} : { sdkCost: usd(sc.sdk_cost_usd) }),
      time: ms(t.ms),
      tokensIn: num(t.tokens_in),
      tokensOut: num(t.tokens_out),
      cacheRead: num(t.cache_read),
    });
    expect([shipped.reply, shipped.stop]).toEqual([json.reply ?? '', json.stop]);
  });
});

// ── PatternStepper: every pattern x ticket x engine ─────────────────────────
describe('PatternStepper steps round-trip', () => {
  const props = patternStepperProps(data.patterns);
  const runs: [string, PatternRunJson, (typeof props.runs)[string][string][string]][] = [];
  for (const [pattern, byTicket] of Object.entries(data.patterns.patterns)) {
    for (const [ticket, byEngine] of Object.entries(byTicket)) {
      for (const [engine, r] of Object.entries(byEngine)) {
        if ('steps' in r) runs.push([`${pattern}/${ticket}/${engine}`, r, props.runs[pattern][ticket][engine]]);
      }
    }
  }

  it('covers every recorded run in patterns.json', () => {
    expect(runs).toHaveLength(5 * 3 * 2);
    expect(runs.reduce((a, [, r]) => a + r.steps.length, 0)).toBe(164);
  });

  it.each(runs)('%s: every step decodes to its patterns.json record, and the totals match', (_name, json, shipped) => {
    if (!shipped.recorded) throw new Error('recorded run shipped as not recorded');
    expect(shipped.steps).toHaveLength(json.steps.length);
    json.steps.forEach((s, i) => {
      const view = patternStepView(shipped.steps[i]);
      if (s.kind === 'model') {
        expect(shipped.steps[i], `step ${i + 1} tuple`).toEqual([0, s.agent, s.role ?? 'call', s.tokens_in, s.tokens_out, s.cache_read, s.cost_usd, s.ms, s.result ?? null]);
        expect(view, `step ${i + 1} text`).toEqual({
          kind: 'model',
          title: `${s.agent} · ${s.role ?? 'call'}`,
          detail: model(s.tokens_in, s.tokens_out, s.cache_read, s.cost_usd, s.ms),
          ...(s.result ? { result: s.result } : {}),
        });
      } else if (s.kind === 'tool') {
        expect(shipped.steps[i], `step ${i + 1} tuple`).toEqual([1, s.agent, s.name, s.args, bit(s.is_error)]);
        expect(view, `step ${i + 1} text`).toEqual({ kind: 'tool', title: `${s.agent} · ${s.name}`, detail: call(s.name, s.args) + (s.is_error ? ' (error)' : '') });
      } else {
        expect(shipped.steps[i]).toEqual([2, s.agent, s.name, s.kind]);
      }
    });
    expect({ calls: shipped.calls, cost: shipped.cost, time: shipped.time, errors: shipped.errors, reply: shipped.reply }).toEqual({
      calls: json.calls,
      cost: usd(json.cost_usd),
      time: ms(json.ms),
      errors: json.errors,
      reply: json.reply ?? '',
    });
  });
});

// ── EngineScorecard: every row's tickets ────────────────────────────────────
describe('EngineScorecard ticket rows round-trip', () => {
  const props = engineScorecardProps(data.engineScores);
  const rows: [string, string, ScoreRunJson, number][] = [];
  for (const [engine, byDesk] of Object.entries(data.engineScores.scores)) {
    for (const [desk, row] of Object.entries(byDesk)) {
      Object.entries(row.runs ?? {}).forEach(([ticket, r], i) => rows.push([`${engine}/${desk}`, ticket, r, i]));
    }
  }

  it('covers every per-ticket run in engine_scores.json', () => {
    expect(rows).toHaveLength(8 * 13);
    expect(Object.values(props.tickets).reduce((a, t) => a + t.length, 0)).toBe(rows.length);
  });

  it.each(rows)('%s %s decodes to its engine_scores.json run', (key, ticket, r, i) => {
    const shipped = props.tickets[key][i];
    expect(shipped).toEqual([ticket, tri(r.task_success), tri(r.category_ok), tri(r.judge_pass), tri(r.trajectory_ok), tri(r.safety_fail), r.cost_usd, r.ms, r.sdk_cost_usd]);
    expect(ticketView(shipped)).toEqual({
      ticket,
      success: yn(r.task_success, 'not scored'),
      category: yn(r.category_ok, 'not scored'),
      judge: yn(r.judge_pass, 'no judge recording'),
      trajectory: yn(r.trajectory_ok, 'not evaluable'),
      safety: yn(r.safety_fail, 'not scored'),
      cost: usd(r.cost_usd),
      ...(r.sdk_cost_usd === null ? {} : { sdkCost: usd(r.sdk_cost_usd) }),
      time: ms(r.ms),
    });
  });
});
