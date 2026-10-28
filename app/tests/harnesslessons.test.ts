import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';
import { BUDGET, SUMMARY_TOKENS, SYSTEM_TOKENS, TOOL_SCHEMA_TOKENS, TURN_SIZES, WINDOW } from '../src/lib/compaction';
import { parseTrace } from '../src/lib/trace';
import { scenarios } from '../src/data/scenarios';
import { harnessFlow } from '../src/data/diagrams/harness-flow';
import sample from '../src/data/sample-trace.jsonl?raw';

// The Harness track: every number a lesson states is pinned here against its source: the harness files'
// constants (read with a regex), the LLMs lab's price table, the sample trace, the aids' data, and the
// harness test files. When a source changes, this test fails and names the lesson to update.
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '../..');
const text = (p: string) => readFileSync(join(repo, p), 'utf8');
const lesson = (n: string) => {
  const dir = join(here, '../src/content/lessons/harness');
  const file = readdirSync(dir).find((f) => f.startsWith(`${n}-`));
  if (!file) throw new Error(`no harness lesson ${n}`);
  return readFileSync(join(dir, file), 'utf8');
};

const py = text('harness/python/harness.py');
const go = text('harness/go/main.go');
const lab = text('llms/python/triage_lab.py');
const num = (s: string) => Number(s.replace(/_/g, ''));
const pyConst = (name: string) => {
  const m = py.match(new RegExp(`^${name} = ([\\d_.]+)`, 'm'));
  if (!m) throw new Error(`no ${name} in harness.py`);
  return num(m[1]);
};
const goConst = (name: string) => {
  const m = go.match(new RegExp(`\\b${name}\\s+= ([\\d.]+)`));
  if (!m) throw new Error(`no ${name} in main.go`);
  return num(m[1]);
};
const commas = (n: number) => n.toLocaleString('en-US');

// The model's prices, from the LLMs lab's PRICES table (dollars per million tokens).
const labPrice = lab.match(/"claude-opus-5-5": \(([\d.]+), ([\d.]+)\)/);
const [P_IN, P_OUT] = [Number(labPrice?.[1]), Number(labPrice?.[2])];
const dollars = (inTok: number, outTok: number) => (inTok * P_IN + outTok * P_OUT) / 1e6;

const MAX_TURNS = pyConst('MAX_TURNS');
const MAX_TOKENS = pyConst('MAX_TOKENS');
const CONTEXT_BUDGET = pyConst('CONTEXT_BUDGET');
const MAX_COST = pyConst('MAX_COST');
const [T_CALLS, T_IN, T_OUT] = (py.match(/^TYPICAL_TASK = \(([\d_]+), ([\d_]+), ([\d_]+)\)/m) ?? []).slice(1).map(num);

describe('harness constants agree across Python, Go and the lab', () => {
  it('prices: $4 / $20 in the lab, harness.py and main.go', () => {
    expect([P_IN, P_OUT]).toEqual([4, 20]);
    expect(py).toMatch(/^MODEL = "claude-opus-5-5"/m);
    expect(py).toMatch(/^PRICE_PER_MTOK = \(4\.00, 20\.00\)/m);
    expect(go).toMatch(/var pricePerMTok = \[2\]float64\{4\.00, 20\.00\}/);
  });

  it('limits match between the two files', () => {
    expect([MAX_TURNS, MAX_TOKENS, CONTEXT_BUDGET, MAX_COST]).toEqual([25, 16000, 150000, 10]);
    expect(go).toMatch(/var maxTurns = 25\b/);
    expect([goConst('maxTokens'), goConst('contextBudget'), goConst('maxCost')]).toEqual([16000, 150000, 10]);
    expect([goConst('typicalCalls'), goConst('typicalIn'), goConst('typicalOut')]).toEqual([T_CALLS, T_IN, T_OUT]);
    expect(goConst('charsPerToken')).toBe(pyConst('CHARS_PER_TOKEN'));
  });
});

describe('lesson 1: the money gate in Try it', () => {
  const typical = T_CALLS * dollars(T_IN, T_OUT);
  const worst = MAX_TURNS * dollars(CONTEXT_BUDGET, MAX_TOKENS);
  const estimate =
    `Estimate: typical $${typical.toFixed(2)} a task (${T_CALLS} calls of ~${commas(T_IN)} input and ~${commas(T_OUT)} output tokens); ` +
    `worst case $${worst.toFixed(2)} if every call stays at CONTEXT_BUDGET (MAX_TURNS ${MAX_TURNS} calls of ${commas(CONTEXT_BUDGET)} input and ${commas(MAX_TOKENS)} output tokens, ` +
    `plus any compaction summaries), at $${P_IN.toFixed(2)} / $${P_OUT.toFixed(2)} per million tokens; the hard stop is --max-cost $${MAX_COST.toFixed(2)}.`;
  const spent = `Spent $0.0000 this session; each call is checked against the cap first, so the session stops at about $${MAX_COST.toFixed(2)}.`;

  it('shows the estimate the harness prints, computed from its constants and the lab prices', () => {
    expect(estimate).toContain('typical $0.10 a task');
    expect(estimate).toContain('worst case $23.00');
    expect(lesson('01')).toContain(`${estimate}\n${spent}\nRun it? [y/N]`);
    // The same lines the Python and Go gate tests expect, word for word.
    expect(text('harness/python/tests/test_gate.py').replace(/"\s*\n\s*"/g, '')).toContain(estimate);
    expect(text('harness/go/gate_test.go').replace(/"\s*\+\s*\n\s*"/g, '')).toContain(estimate);
  });

  it('says it costs money, and states the 25 turns, 150,000 and 16,000 and the $10 cap', () => {
    const l1 = lesson('01');
    expect(l1).toMatch(/This step calls the API, so it costs money/);
    expect(l1).toContain(`all ${MAX_TURNS} turns with the full ${commas(CONTEXT_BUDGET)}-token history and a full ${commas(MAX_TOKENS)}-token reply`);
    expect(l1).toContain(`That cap is $${MAX_COST} unless`);
    expect(l1).toContain('.venv-harness/bin/pip install -r harness/python/requirements.txt');
  });
});

describe('lesson 2: prices and the one-call cost', () => {
  it('$4 and $20 per million, and the 200-token call at most $0.004 of output', () => {
    const l2 = lesson('02');
    expect(l2).toContain(`Opus 5.5 costs $${P_IN} per million input tokens and $${P_OUT} per million output tokens`);
    expect(l2).toContain('max_tokens=200,');
    expect(dollars(0, 200)).toBeCloseTo(0.004, 10);
    expect(l2).toContain('at most 200 output tokens, which is $0.004 at $20 per million');
    expect(l2).toContain(`\`MAX_TOKENS = 16_000\``);
    expect(py).toMatch(/^MAX_TOKENS = 16_000\b/m);
    expect(l2).toContain('`PRICE_PER_MTOK = (4.00, 20.00)`');
    expect(l2).toContain('`MAX_COST = 10.00`');
  });
});

describe('lessons 3 to 6: caps, turns, timings', () => {
  it('20,000 characters (Python) and bytes (Go)', () => {
    expect(py).toContain('.read_text()[:20_000]');
    expect(go).toMatch(/if len\(b\) > 20000 \{/);
    expect(lesson('03')).toContain('20,000 characters');
    expect(lesson('06')).toContain('first 20,000 characters');
    expect(lesson('06')).toContain('first 20,000 bytes');
  });

  it('MAX_TURNS = 25 (lesson 4)', () => {
    expect(lesson('04')).toContain(`\`MAX_TURNS = ${MAX_TURNS}\``);
    expect(lesson('04')).toContain(`after ${MAX_TURNS} model calls`);
  });

  it('Wrote 100 characters (lesson 5) is the length of the fixed README', () => {
    const write = scenarios['approve-write'].turns
      .flatMap((t) => t.blocks)
      .find((b) => b.type === 'tool_use' && b.name === 'write_file');
    const content = write && write.type === 'tool_use' ? write.input.content : '';
    expect(content.length).toBe(100);
    expect(lesson('05')).toContain(`\`Wrote ${content.length} characters to README.md\``);
  });

  it('the 30-second deadline and max_workers=8 (lesson 6)', () => {
    expect(go).toMatch(/toolTimeout\s+= 30 \* time\.Second/);
    expect(py).toContain('ThreadPoolExecutor(max_workers=8)');
    expect(lesson('06')).toContain('30-second context deadline');
    expect(lesson('06')).toContain('`ThreadPoolExecutor(max_workers=8)`');
  });

  it('the timeline: 300, 1200 and 500 ms; 2000 in a row, 1200 in parallel (lesson 6)', () => {
    const tl = readFileSync(join(here, '../src/components/Timeline.astro'), 'utf8');
    const ms = [...tl.matchAll(/ms: (\d+) \}/g)].map((m) => Number(m[1]));
    expect(ms).toEqual([300, 1200, 500]);
    const seq = ms.reduce((a, b) => a + b, 0);
    const par = Math.max(...ms);
    const l6 = lesson('06');
    expect(l6).toContain(`${ms[0]}, ${ms[1]} and ${ms[2]} ms`);
    expect(l6).toContain(`The parallel lane ends at ${par} ms`);
    expect(l6).toContain(`about ${seq} ms`);
    expect(l6).toContain(`instead of the ${seq} ms a one-at-a-time loop would need`);
  });
});

describe('lesson 7: the context meter and the budget', () => {
  it('1,900 base, 126,900 after five reads, 151,900 after six, 3,000 summary', () => {
    const base = SYSTEM_TOKENS + TOOL_SCHEMA_TOKENS;
    const l7 = lesson('07');
    expect(l7).toContain(`${commas(base + 5 * TURN_SIZES.file)} tokens: ${commas(base)} for the system prompt and tool schemas, plus five turns of ${commas(TURN_SIZES.file)}`);
    expect(l7).toContain(`The sixth would make ${commas(base + 6 * TURN_SIZES.file)}`);
    expect(l7).toContain(`**Summary** shows ${commas(SUMMARY_TOKENS)}`);
    expect(l7).toContain(`**Budget ${commas(BUDGET)}**`);
  });

  it('the window, CONTEXT_BUDGET and the 4,000-token summary match the harness', () => {
    expect(BUDGET).toBe(CONTEXT_BUDGET);
    const l7 = lesson('07');
    expect(l7).toContain(`it is ${commas(WINDOW)} tokens`);
    expect(l7).toContain(`\`CONTEXT_BUDGET\`, ${commas(CONTEXT_BUDGET)} input tokens`);
    expect(py).toContain('tool_choice={"type": "none"}, max_tokens=4_000');
    expect(go).toContain('MaxTokens:  4000,');
    expect(l7).toContain('the 4,000-token cap');
  });
});

describe('lesson 8: the sample trace', () => {
  const events = parseTrace(sample);
  const t0 = events[0].ts;
  const of = (name: string) => events.filter((e) => e.event === name);
  const responses = of('model_response');
  const requests = of('model_request');
  const tools = of('tool_result');

  it('is serialised: a turn that writes runs nothing else, and the tests run a turn later', () => {
    // Group tool_result events by the model_response before them.
    const turns: string[][] = [];
    for (const e of events) {
      if (e.event === 'model_response') turns.push([]);
      if (e.event === 'tool_result') turns[turns.length - 1].push(String(e.tool));
    }
    expect(turns).toEqual([['read_file', 'run_tests'], ['write_file'], ['run_tests'], []]);
  });

  it('states its figures as the trace has them', () => {
    const l8 = lesson('08');
    expect(requests.map((r) => r.messages)).toEqual([1, 3, 5, 7]);
    expect(l8).toMatch(/sends 1 message[\s\S]*now holds 3 messages[\s\S]*5 messages go out[\s\S]*7 messages go out/);
    const firstWait = responses[0].ts - requests[0].ts;
    expect(l8).toContain(`after about ${firstWait.toFixed(1)} seconds`);
    const tokensIn = responses.map((r) => r.input_tokens);
    expect(tokensIn).toEqual([1942, 3410, 3702, 4102]);
    expect(l8).toContain(`\`input_tokens=${tokensIn[0]}\``);
    expect(l8).toContain(`(${tokensIn.join(', ')})`);
    expect(tools.map((t) => [t.tool, t.ms, t.is_error])).toEqual([
      ['read_file', 4, false],
      ['run_tests', 1318, true],
      ['write_file', 6, false],
      ['run_tests', 1301, false],
    ]);
    expect(l8).toContain('`read_file` in 4 ms and `run_tests` in 1318 ms');
    expect(l8).toContain('The write takes 6 ms');
    expect(l8).toContain('passes in 1301 ms');
    const permissionGap = of('permission')[0].ts - responses[1].ts;
    expect(l8).toContain(`about ${permissionGap.toFixed(1)} seconds after the reply arrived`);
    const end = of('session_end')[0];
    expect(end.turns).toBe(responses.length);
    expect(l8).toContain(`\`session_end\` records ${end.turns} turns`);
    const total = events[events.length - 1].ts - t0;
    expect(l8).toContain(`most of the ${total.toFixed(1)} seconds`);
    const wait = responses.reduce((a, r, i) => a + (r.ts - requests[i].ts), 0);
    expect(l8).toContain(`the four \`model_request\` to \`model_response\` gaps add up to about ${wait.toFixed(1)} seconds`);
  });

  it('is captioned as a hand-written sample, not a recording', () => {
    expect(lesson('08')).toContain('It is a hand-written sample in the exact format the Python harness writes, not a recording of a real run.');
    const view = readFileSync(join(here, '../src/components/TraceView.astro'), 'utf8');
    expect(view).toContain("<caption>A hand-written sample session, in the harness's trace.jsonl format</caption>");
  });
});

describe('lesson 9: costs, sizes and the test count', () => {
  it('the sample trace totals 13,156 in and 589 out: $0.0644', () => {
    const responses = parseTrace(sample).filter((e) => e.event === 'model_response');
    const tokensIn = responses.reduce((a, r) => a + Number(r.input_tokens), 0);
    const tokensOut = responses.reduce((a, r) => a + Number(r.output_tokens), 0);
    const cost = dollars(tokensIn, tokensOut);
    expect([tokensIn, tokensOut, cost.toFixed(4)]).toEqual([13156, 589, '0.0644']);
    expect(lesson('09')).toContain(`(${commas(tokensIn)} in, ${tokensOut} out) that is $${cost.toFixed(4)}`);
    expect(lesson('09')).toContain(`\`[$${cost.toFixed(4)} this task, $${cost.toFixed(4)} this session]\``);
  });

  it('the quiz cost: 10,000 in and 600 out is $0.052', () => {
    const l9 = lesson('09');
    expect(l9).toContain('input_tokens 2,000, 3,500 and 4,500, and output_tokens 200, 300 and 100');
    expect(dollars(2000 + 3500 + 4500, 200 + 300 + 100)).toBeCloseTo(0.052, 10);
    expect(l9).toContain('The total is $0.052.');
  });

  it('about 320 and 550 lines', () => {
    const lines = (s: string) => s.trimEnd().split('\n').length;
    const l9 = lesson('09');
    const stated = l9.match(/The Python file is about (\d+) lines[\s\S]*?The Go file is about (\d+) lines/);
    expect(stated).not.toBeNull();
    expect(Math.abs(lines(py) - Number(stated![1]))).toBeLessThanOrEqual(10);
    expect(Math.abs(lines(go) - Number(stated![2]))).toBeLessThanOrEqual(10);
  });

  it('"28 passed" is the number of Python test cases', () => {
    const dir = join(repo, 'harness/python/tests');
    let cases = 0;
    for (const f of readdirSync(dir).filter((f) => /^test_.*\.py$/.test(f))) {
      const src = readFileSync(join(dir, f), 'utf8');
      cases += (src.match(/^def test_/gm) ?? []).length;
      for (const m of src.matchAll(/@pytest\.mark\.parametrize\("\w+", \[([^\]]*)\]\)/g)) cases += m[1].split(',').length - 1;
    }
    expect(cases).toBe(28);
    expect(lesson('09')).toContain(`You should see \`${cases} passed\` for Python`);
  });
});

describe('every Try it command runs in the harness venv', () => {
  it('no bare python, pip or .venv in a harness lesson command', () => {
    for (const n of ['01', '02', '03', '04', '05', '06', '07', '08', '09']) {
      const blocks = [...lesson(n).matchAll(/```(?:bash|sh)\n([\s\S]*?)```/g)].map((m) => m[1]).join('\n');
      const inline = [...lesson(n).matchAll(/`([^`\n]*\bpython[^`\n]*)`/g)].map((m) => m[1]);
      for (const cmd of [...blocks.split('\n'), ...inline]) {
        const bare = cmd.replace(/(\.\.\/\.\.\/)?\.venv-harness\/bin\/(python|pip)/g, '').replace(/python3 -m venv \.venv-harness/g, '');
        expect(bare, `lesson ${n}: ${cmd}`).not.toMatch(/(^|[\s;&(])(python3?|pip)\s/);
        expect(cmd, `lesson ${n}: ${cmd}`).not.toMatch(/source \.venv|\.venv\/bin/);
      }
    }
  });
});

// M1 (P14): the quizzes state numbers too. Each lesson's quiz phrases are pinned here, every number computed
// from the same sources as the body pins above (the harness constants, the lab prices, the aids' data); a wrong
// option's number is written out as the wrong option it is, and the HTTP codes are fixed facts. Every data
// number in a quiz must be in one of its lesson's phrases.
type Quiz = { question: string; options: string[]; answer: number; explanation: string }[];
const quizText = (n: string) =>
  (matter(lesson(n)).data.quiz as Quiz).flatMap((q) => [q.question, ...q.options, q.explanation]).join(' | ').replace(/\s+/g, ' ');
/** The numbers a quiz states that carry data: decimals, thousands, and integers of two or more digits.
 *  Single digits are structure ("step 5", "Three tool calls"). */
const NUMBER = /\d+(?:,\d{3})*(?:\.\d+)?/g;
const quizNumbers = (n: string) => [...quizText(n).matchAll(NUMBER)].map((m) => m[0]).filter((x) => /[.,]/.test(x) || x.length >= 2);

describe('the quizzes: every number they state is pinned (M1)', () => {
  const last = harnessFlow.steps[harnessFlow.steps.length - 1];
  const ms = [...readFileSync(join(here, '../src/components/Timeline.astro'), 'utf8').matchAll(/ms: (\d+) \}/g)].map((m) => Number(m[1]));
  const seq = ms.reduce((a, b) => a + b, 0);
  const summaryCap = num(/max_tokens=([\d_]+)/.exec(py.slice(py.indexOf('def compact')))![1]);
  const over = CONTEXT_BUDGET + 2_000; // a made-up reading just over the budget
  const ins = [2_000, 3_500, 4_500];
  const outs = [200, 300, 100];
  const tin = ins.reduce((a, b) => a + b, 0);
  const tout = outs.reduce((a, b) => a + b, 0);
  const and3 = (x: string[]) => `${x[0]}, ${x[1]} and ${x[2]}`;

  it('the facts behind the phrases', () => {
    expect(last.caption).toMatch(/final answer is shown to you/); // "step 10 ... shows the answer"
    expect(over).toBeGreaterThan(CONTEXT_BUDGET);
    expect(WINDOW).toBe(1_000_000);
    expect(dollars(tin, tout)).toBeCloseTo(0.052, 10);
  });

  const PINNED: Record<string, string[]> = {
    '01': [`end_turn jumps to step ${last.n} and shows the answer`, `Straight to step ${last.n}, to show the answer` /* a wrong option */,
      `The loop only reaches step ${last.n} when the router sees end_turn`],
    '04': [`ends the loop after ${MAX_TURNS} model calls`, `costs at most ${MAX_TURNS} loop calls`],
    '06': [`Three tool calls take ${ms[0]}, ${ms[1]} and ${ms[2]} ms`, `${seq} ms, because each call waits for the one before` /* a wrong option */,
      `${Math.max(...ms)} ms, the length of the slowest call`, `${Math.min(...ms)} ms, the length of the fastest call` /* a wrong option */,
      `${Math.round(seq / ms.length)} ms, the average` /* a wrong option */,
      `about ${Math.max(...ms)} ms instead of the ${seq} ms a one-at-a-time loop would need`],
    '07': [`input_tokens is ${commas(over)}`, `fits under ${commas(CONTEXT_BUDGET)}` /* a wrong option */, `${commas(over)} is over CONTEXT_BUDGET`,
      `max_tokens ${summaryCap}`, `context window is ${commas(WINDOW)} tokens. Why does the harness compact at ${commas(CONTEXT_BUDGET)}?`,
      `refuses requests over ${commas(CONTEXT_BUDGET)} tokens` /* a wrong option */, `past ${commas(CONTEXT_BUDGET)} tokens` /* a wrong option */,
      `${commas(CONTEXT_BUDGET)} is the most that max_tokens allows` /* a wrong option */],
    '08': ['would catch every 429', 'A 429 is both a RateLimitError and an APIStatusError', 'with the 429 case above', 'The API answers 529 (overloaded) twice'],
    '09': [`input_tokens ${and3(ins.map(commas))}, and output_tokens ${and3(outs.map(String))}`,
      `At $${P_IN} per million input tokens and $${P_OUT} per million output tokens`,
      `About $${dollars(0, tout).toFixed(3)}` /* a wrong option: output only */, `About $${((tin * P_OUT) / 1e6).toFixed(2)}` /* a wrong option: input at the output price */,
      `About $${dollars(tin, tout).toFixed(3)}`, `About $${dollars(tin, 0).toFixed(3)}` /* a wrong option: input only */,
      `Input is ${commas(tin)} tokens, which costs ${commas(tin)} × ${P_IN} / 1,000,000 = $${dollars(tin, 0).toFixed(2)}`,
      `Output is ${tout} tokens, which costs ${tout} × ${P_OUT} / 1,000,000 = $${dollars(0, tout).toFixed(3)}`,
      `The total is $${dollars(tin, tout).toFixed(3)}`],
  };

  for (const l of ['01', '02', '03', '04', '05', '06', '07', '08', '09']) {
    it(`lesson ${l}: each stated phrase holds, and every number in the quiz is one of them`, () => {
      const q = quizText(l);
      for (const phrase of PINNED[l] ?? []) expect(q, l).toContain(phrase);
      const covered = new Set((PINNED[l] ?? []).flatMap((x) => x.match(NUMBER) ?? []));
      for (const x of quizNumbers(l)) expect(covered.has(x), `lesson ${l}: quiz number ${x} is not pinned`).toBe(true);
    });
  }
});
