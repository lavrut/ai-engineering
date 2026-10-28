import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';

// The MCP track, lessons 1-11: every number a lesson states is pinned here against its source, so a
// change to the code, the data or a recording fails this test and names the lesson to update. Sources:
//  - mcp/data/derived/*.json, written by mcp/python/derive.py (never by hand). lesson_numbers.json holds
//    the client output, md5 and record-mode estimates the Try-it sections quote;
//  - the recordings in mcp/data/recordings/ and mcp/data/simulated/ (token counts, answers);
//  - constants read from the source files by regex (limits, ports, timeouts, versions);
//  - pytest pass counts: stated here as the lesson states them, and checked against a real collection by
//    mcp/python/tests/test_lesson_counts.py, which runs each lesson's own pytest command with --collect-only.
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '../..');
const lessonsDir = join(here, '../src/content/lessons/mcp');
const src = (p: string) => readFileSync(join(repo, p), 'utf8');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const derived = (name: string): any => JSON.parse(src(`mcp/data/derived/${name}.json`));
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const jsonl = (p: string): any[] => src(p).trim().split('\n').map((l) => JSON.parse(l));
const read = (n: string) => readFileSync(join(lessonsDir, `${n}.mdx`), 'utf8');
const body = (n: string) => matter(read(n)).content;
const commas = (n: number) => n.toLocaleString('en-US');
// A Python constant `NAME = 123` (underscores allowed) in a source file.
const constant = (p: string, name: string): number => {
  const m = new RegExp(`^${name}\\s*=\\s*([\\d_.]+)`, 'm').exec(src(p));
  if (!m) throw new Error(`${name} not found in ${p}`);
  return Number(m[1].replace(/_/g, ''));
};
const price = (model: string): [number, number] => {
  const m = new RegExp(`"${model}": \\(([\\d.]+), ([\\d.]+)\\)`).exec(src('llms/python/triage_lab.py'));
  if (!m) throw new Error(`no price for ${model}`);
  return [Number(m[1]), Number(m[2])];
};
const cost = (model: string, tin: number, tout: number) => {
  const [pin, pout] = price(model);
  return (tin * pin + tout * pout) / 1e6;
};
const usage = (p: string) => jsonl(p).map((l) => l.response.usage as { input_tokens: number; output_tokens: number });
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

const LESSONS = ['01-what', '02-first-server', '03-resources-prompts', '04-client', '05-agent', '06-hosts',
  '07-http', '08-write-tools', '09-auth', '10-apps', '11-capstone'];
const numbers = derived('lesson_numbers');
const budget = derived('tool_budget').servers;
const injection = derived('injection');
const CORE = 'mcp/core/python/lumen_core/__init__.py';
const RESEARCH = 'docs/superpowers/research/2026-10-26-mcp-facts.md';

describe('the MCP lessons as a set', () => {
  it('run every Python, pip and pytest command in .venv-mcp (P1), like the README', () => {
    for (const l of LESSONS) {
      const blocks = [...body(l).matchAll(/```bash\n([\s\S]*?)```/g)].map((m) => m[1]);
      for (const line of blocks.join('\n').split('\n')) {
        if (line.startsWith('python3 -m venv .venv-mcp')) continue; // creating the venv itself
        const rest = line.replaceAll('.venv-mcp/bin/python', '').replaceAll('.venv-mcp/bin/pip', '').replaceAll('-m pytest ', '');
        const bare = /(^|[\s(;&|'])(python3?|pip3?|pytest)\s/.exec(rest);
        expect(bare, `${l}: ${line}`).toBeNull();
      }
    }
    expect(src('README.md')).toContain('python3 -m venv .venv-mcp && .venv-mcp/bin/pip install -r mcp/requirements.txt');
  });

  it('spread the quiz answers over all four positions (M12): no position is right more than 9 times of 33', () => {
    const counts = [0, 0, 0, 0];
    for (const l of LESSONS) {
      const quiz = matter(read(l)).data.quiz as { options: string[]; answer: number }[];
      for (const q of quiz) {
        expect(q.options, l).toHaveLength(4);
        counts[q.answer]++;
      }
      expect(new Set(quiz.map((q) => q.answer)).size, `${l} uses one position only`).toBeGreaterThan(1);
    }
    expect(sum(counts)).toBe(33);
    for (const c of counts) expect(c).toBeLessThanOrEqual(9);
  });
});

describe('lesson 1 (what MCP is): the numbers it states', () => {
  const l = body('01-what');
  it('60 lab tickets plus t061', () => {
    const lab = jsonl('llms/data/tickets.jsonl');
    expect(lab).toHaveLength(60);
    expect(jsonl('mcp/data/injection.jsonl').map((t) => t.id)).toEqual(['t061']);
    expect(l).toContain('over the 60 Lumen tickets plus an extra injection test ticket, `t061`');
  });

  it('Python 3.12, Go 1.27.2 and Node 22.19; Inspector 2.12.0 lists 3 tools in both eras', () => {
    expect(src('mcp/requirements.txt')).toMatch(/^# Python 3\.12\+ required/m);
    expect(src('mcp/go/go.mod')).toMatch(/^go 1\.27\.2$/m);
    const smoke = src('mcp/scripts/inspector_smoke.sh');
    expect(smoke).toContain('INSPECTOR="@modelcontextprotocol/inspector@2.12.0"');
    expect(smoke).toContain('older than 22.19');
    expect(smoke).toMatch(/^EXPECT=3$/m);
    expect(budget['lumen-read'].tools).toBe(3);
    expect(l).toContain('Python 3.12 or later and Go 1.27.2 or later');
    expect(l).toContain('Node 22.19 or later');
    for (const s of ['`Inspector 2.12.0`', '`ok   (modern): tools/list returned 3 tools`',
      '`ok   (legacy): tools/list returned 3 tools`', '`PASS: both protocol eras list 3 tools`']) expect(l).toContain(s);
  });

  it('a version mismatch is code -32022 (the research sheet)', () => {
    expect(src(RESEARCH)).toContain('`UnsupportedProtocolVersionError` (code -32022)');
    expect(l).toContain('`UnsupportedProtocolVersionError`, code -32022');
  });
});

describe('lesson 2 (first server): the numbers it states', () => {
  const l = body('02-first-server');
  it('35 contract cases for lumen-read', () => {
    expect(jsonl('mcp/fixtures/contract.jsonl').filter((c) => c.server === 'read')).toHaveLength(35);
    expect(l).toContain('35 cases for `lumen-read`');
  });

  it('the limits: query 1 to 200, limit 1 to 20 (default 5), text 1 to 20,000', () => {
    expect(constant(CORE, 'MAX_QUERY_CHARS')).toBe(200);
    expect(src(CORE)).toContain('MIN_LIMIT, MAX_LIMIT, DEFAULT_LIMIT = 1, 20, 5');
    expect(constant(CORE, 'MAX_TEXT_CHARS')).toBe(20000);
    expect(l).toContain('`query` 1 to 200 characters; `limit` 1 to 20, default 5');
    expect(l).toContain('`text` 1 to 20,000 characters');
    for (const e of ['query must be 1 to 200 characters', 'limit must be 1 to 20', 'text must be 1 to 20000 characters'])
      expect(l).toContain(`\`${e}\``);
  });

  it('"charged twice" (limit 2) finds t001 and t007; the classifier gives six scores, low confidence, v1', () => {
    expect(numbers.search_charged_twice.arguments).toEqual({ query: 'charged twice', limit: 2 });
    expect(numbers.search_charged_twice.ids).toEqual(['t001', 't007']);
    expect(l).toContain('We got `t001` and `t007`');
    expect(numbers.classify).toMatchObject({ low_confidence: true, scores: 6, model_version: 'v1' });
    expect(l).toContain('`"low_confidence": true`, the six scores, and `"model_version": "v1"`');
    expect(l).toContain(`'{"text": "${numbers.classify.arguments.text}"}'`);
  });
});

describe('lesson 3 (resources and prompts): the numbers it states', () => {
  const l = body('03-resources-prompts');
  const summary = JSON.parse(src('llms/data/inspect_summary.json'));
  it('four runs in the eval report; v2_rules scores category 1 and priority 0.9611 in both', () => {
    const ids = summary.runs.map((r: { id: string }) => r.id);
    expect(ids).toEqual(['v1_basic', 'v2_rules', 'v3_examples', 'judge']);
    expect(l).toContain('four runs (`v1_basic`, `v2_rules`, `v3_examples` and `judge`)');
    const v2 = summary.runs.find((r: { id: string }) => r.id === 'v2_rules').rows;
    const row = (m: string) => v2.find((r: { metric: string }) => r.metric === m);
    expect([row('category').lab, row('category').inspect]).toEqual([1, 1]);
    expect([row('priority').lab, row('priority').inspect]).toEqual([0.9611, 0.9611]);
    expect(l).toContain('(`category` 1, `priority` 0.9611)');
  });

  it('reads say ttlMs 0 and cacheScope private; a missing resource is -32602 (was -32002)', () => {
    const r = derived('messages').exchanges.find((x: { method: string }) => x.method === 'resources/read').response.result;
    expect([r.ttlMs, r.cacheScope]).toEqual([0, 'private']);
    expect(l).toContain('`"ttlMs": 0` and `"cacheScope": "private"`');
    expect(src(RESEARCH)).toContain('Resource-not-found is now -32602 (was -32002)');
    expect(l).toContain('the code -32602 (INVALID_PARAMS). The 2026-07-28 revision uses -32602 for this (it was -32002)');
  });
});

describe('lesson 4 (client): the numbers it states', () => {
  it('the client prints bytes whose md5 is the one quoted (derive.py runs the same command in memory)', () => {
    expect(numbers.search_charged_twice.md5).toBe('b0a7e0fab66d59fd1c32497fbabb1392');
    expect(body('04-client')).toContain(`got \`${numbers.search_charged_twice.md5}\` each time`);
    expect(body('04-client')).toContain(`call search_tickets '{"query": "charged twice", "limit": 2}'`);
  });
});

describe('lesson 5 (agent): the numbers it states', () => {
  const l = body('05-agent');
  const calls = usage('mcp/data/recordings/lesson5.jsonl');
  it('two calls: 779 in / 172 out, then 1,646 / 223; 2,425 and 395 in total; about $0.00044 at $0.10 / $0.50', () => {
    expect(calls.map((u) => [u.input_tokens, u.output_tokens])).toEqual([[779, 172], [1646, 223]]);
    expect(l).toContain('| 1 | 779 | 172 | `tool_use` |');
    expect(l).toContain('| 2 | 1,646 | 223 | `end_turn` |');
    const tin = sum(calls.map((u) => u.input_tokens));
    const tout = sum(calls.map((u) => u.output_tokens));
    expect(l).toContain(`That is ${commas(tin)} input tokens and ${commas(tout)} output tokens`);
    expect(price('claude-haiku-5-5')).toEqual([0.1, 0.5]);
    expect(l).toContain('($0.10 per million input tokens, $0.50 per million output');
    expect(cost('claude-haiku-5-5', tin, tout).toFixed(5)).toBe('0.00044');
    expect(l).toContain('cost about $0.00044');
    expect(l).toContain('cost about $0.00044, so it came in under the typical figure');
  });

  it('quotes the recorded answer word for word (about 0.29, Credit card at 0.27)', () => {
    const last = jsonl('mcp/data/recordings/lesson5.jsonl').at(-1).response.content;
    const text: string = last.filter((b: { type: string }) => b.type === 'text').map((b: { text: string }) => b.text).join('');
    const paras = text.split('\n\n');
    expect(paras).toHaveLength(2);
    for (const p of paras) expect(l).toContain(`> ${p}`);
    expect(text).toContain('about 0.29');
    expect(text).toContain('Credit card at 0.27');
  });

  it('the estimate: ~832 first, $0.0010 for 3 calls, $0.0748 worst over 10 calls of 8,000 + 4,000 tokens', () => {
    const e = numbers.estimates.lesson5;
    expect(e).toMatchObject({ model: 'claude-haiku-5-5', first: 832, calls: 3, typical: 0.001, max_calls: 10, worst: 0.0748 });
    expect(l).toContain(`first request about ${e.first} input tokens, typical \`$${e.typical.toFixed(4)}\` for ${e.calls} calls`);
    expect(l).toContain(`worst case \`$${e.worst.toFixed(4)}\` if all ${e.max_calls} allowed calls`);
    expect(l).toContain(`The real recording made ${calls.length} calls, not ${e.calls}`);
    expect(constant('mcp/python/agent.py', 'WORST_RESULT_TOKENS')).toBe(8000);
    expect(constant('mcp/python/agent.py', 'MAX_TOKENS')).toBe(4000);
    expect(l).toContain('up to 8,000 tokens of tool results on every round and 4,000 output tokens on every call');
  });

  it('CALL_TIMEOUT is 60 seconds', () => {
    expect(constant('mcp/python/agent.py', 'CALL_TIMEOUT')).toBe(60);
    expect(l).toContain('`CALL_TIMEOUT` (60 seconds)');
  });
});

describe('lesson 6 (hosts): the numbers it states', () => {
  const l = body('06-hosts');
  const r = budget['lumen-read'];
  const o = budget['lumen-ops'];
  it('lumen-read 3 tools, 2,095 chars, ~524 tokens; lumen-ops 1, 705, ~177; together 4 and ~701', () => {
    expect([r.tools, r.chars, r.tokens_estimate, o.tools, o.chars, o.tokens_estimate]).toEqual([3, 2095, 524, 1, 705, 177]);
    expect(l).toContain(`\`lumen-read\` has ${r.tools} tools, ${commas(r.chars)} characters, about ${r.tokens_estimate} tokens`);
    expect(l).toContain(`\`lumen-ops\` has ${o.tools} tool, ${o.chars} characters, about ${o.tokens_estimate} tokens`);
    expect(l).toContain(`sends ${r.tools + o.tools} tool definitions, about ${r.tokens_estimate + o.tokens_estimate} estimated tokens`);
  });

  it('roughly 175 per tool; 524 of the 779 in lesson 5; ten servers about 5,240', () => {
    expect(Math.round((r.tokens_estimate + o.tokens_estimate) / (r.tools + o.tools))).toBe(175);
    expect(l).toContain('roughly 175 estimated tokens');
    const first = usage('mcp/data/recordings/lesson5.jsonl')[0].input_tokens;
    expect(l).toContain(`was ${first} input tokens. On our estimate the three tools are about ${r.tokens_estimate} of those ${first}`);
    expect(l).toContain(`about ${commas(10 * r.tokens_estimate)} estimated tokens`);
  });

  it('the clash script: 4 real tools plus a made-up server of 2 gives 5 flat and 6 prefixed', () => {
    const extra = /servers\["other-crm"\] = \[([^\]]*)\]/.exec(l);
    const n = (extra?.[1].split(',').length ?? 0) + r.tools + o.tools;
    expect(n).toBe(6);
    expect(l).toContain(`\`flat registry: ${n - 1} tools; prefixed: ${n} tools\``);
    expect(l).toContain('The prefixed one kept all six.');
  });
});

describe('lesson 7 (HTTP): the numbers it states', () => {
  const l = read('07-http');
  it('the body cap is 131,072 bytes; the test file is one byte over; ports 8765 and 8766; Inspector 6274', () => {
    const cap = constant(CORE, 'MAX_BODY_BYTES');
    expect(cap).toBe(131072);
    expect(l).toContain(`over \`${commas(cap)}\` bytes`);
    expect(l).toContain(`cap the request body at ${commas(cap)} bytes`);
    expect(l).toContain(`Make a file of ${commas(cap + 1)} spaces`);
    expect(l).toContain(`head -c ${cap + 1} /dev/zero`);
    expect(l).toContain(`\`MAX_BODY_BYTES\` is \`${cap}\``);
    expect(src('mcp/python/resource_auth.py')).toContain('READ_PORT, OPS_PORT = 8765, 8766');
    expect(src('mcp/python/resource_auth.py')).toContain('INSPECTOR_ORIGINS = ("http://127.0.0.1:6274", "http://localhost:6274")');
    expect(l).toContain('`http://127.0.0.1:6274` and `http://localhost:6274`');
  });
});

describe('lesson 8 (write tools): the numbers it states', () => {
  const l = body('08-write-tools');
  it('a state lives five minutes (300 s) and is forgotten 60 s after; Go signs with a 32-byte key', () => {
    expect(constant('mcp/python/ops_server.py', 'STATE_TTL_SECONDS')).toBe(300);
    expect(l).toContain('five minutes (`STATE_TTL_SECONDS = 300`)');
    expect(constant('mcp/python/ops_server.py', 'PRUNE_MARGIN_SECONDS')).toBe(60);
    expect(l).toContain('`PRUNE_MARGIN_SECONDS` (60 seconds)');
    expect(src('mcp/go/opsserver/server.go')).toContain('key := make([]byte, 32)');
    expect(l).toContain('a 32-byte key made at start');
  });

  it('states 3 passed for its pytest selection (counted by test_lesson_counts.py)', () => {
    expect(l).toContain('We got `3 passed` in Python');
  });
});

describe('lesson 9 (auth): the numbers it states', () => {
  const l = body('09-auth');
  it('30 s of skew on nbf and iat; a 32-byte key at mode 0600; the AS on 9000', () => {
    expect(constant('mcp/python/resource_auth.py', 'LEEWAY_SECONDS')).toBe(30);
    expect(l).toContain('`nbf` and `iat` allow 30 seconds of clock skew');
    expect(l).toContain('`LEEWAY_SECONDS` (30)');
    expect(constant('mcp/python/resource_auth.py', 'KEY_BYTES')).toBe(32);
    expect(l).toContain('writes 32 random bytes, as hex');
    expect(src('mcp/python/auth_server.py')).toContain('HOST, PORT = "127.0.0.1", 9000');
    expect(l).toContain('`mcp/python/auth_server.py` on `127.0.0.1:9000`');
  });

  it('the one-hit search is t001 in billing', () => {
    expect(numbers.search_charged_twice_1).toEqual([{ id: 't001', category: 'billing' }]);
    expect(l).toContain('one result, `t001`, in `billing`');
  });

  it('states 8 passed for its pytest selection (I2; counted by test_lesson_counts.py)', () => {
    expect(l).toContain('We got `8 passed`.');
    expect(l).not.toContain('6 passed');
  });

  it('states the loopback-only rule of the test AS and why (P5)', () => {
    expect(l).toContain('redirect URI whose scheme is `http` and whose host is `127.0.0.1` or `localhost`');
    expect(l).not.toContain('CORS `*`');
    const as = src('mcp/python/auth_server.py');
    expect(as).toContain('LOOPBACK_HOSTS = ("127.0.0.1", "localhost")');
    expect(as).toContain('RegistrationError("invalid_redirect_uri"');
  });
});

describe('lesson 10 (apps): the numbers it states', () => {
  const l = body('10-apps');
  const html: string = derived('app_snapshot').html;
  it('the page is 7,225 bytes, speaks extension version 2026-01-26, offers the six categories', () => {
    const bytes = Buffer.byteLength(html, 'utf8');
    expect(bytes).toBe(7225);
    expect(l).toContain(`It is ${commas(bytes)} bytes of HTML`);
    expect(l).toContain(`\`html: ${bytes} bytes; starts <!doctype html>\``);
    expect(html).toContain('var PROTOCOL_VERSION = "2026-01-26";');
    expect(l).toContain('speaks version `2026-01-26` of the extension');
    expect(html.match(/<option value=/g)).toHaveLength(6);
    expect(src(CORE)).toContain('CATEGORIES = ["billing", "bug", "account", "how_to", "feature_request", "outage"]');
    expect(l).toContain('offers the six categories');
  });

  it('mcp 2.3.0 and ext-apps v2.0.3 (the research sheet); 5 passed (counted by test_lesson_counts.py)', () => {
    expect(src('mcp/requirements.txt')).toMatch(/^mcp==2\.3\.0$/m);
    expect(l).toContain('`mcp` 2.3.0');
    expect(src(RESEARCH)).toContain('npm `@modelcontextprotocol/ext-apps` latest = 2.0.3');
    expect(l).toContain('Pin the tag `v2.0.3`');
    expect(l).toContain('We got `5 passed`.');
  });
});

describe('lesson 11 (capstone): the numbers it states', () => {
  const l = read('11-capstone');
  const off = usage('mcp/data/recordings/capstone-noguard.jsonl');
  const on = usage('mcp/data/recordings/capstone-guard.jsonl');
  it('each Haiku run: 2 calls, 2,147 in; 308 out off, 363 on; about $0.0004', () => {
    expect([off.length, on.length]).toEqual([2, 2]);
    const tin = sum(off.map((u) => u.input_tokens));
    expect(sum(on.map((u) => u.input_tokens))).toBe(tin);
    const [oOff, oOn] = [sum(off.map((u) => u.output_tokens)), sum(on.map((u) => u.output_tokens))];
    expect(l).toContain(`Each run made 2 model calls, with ${commas(tin)} input tokens in total`);
    expect(l).toContain(`The output was ${oOff} tokens with the guard off and ${oOn} with it on`);
    expect(cost('claude-haiku-5-5', tin, oOff).toFixed(4)).toBe('0.0004');
    expect(cost('claude-haiku-5-5', tin, oOn).toFixed(4)).toBe('0.0004');
    expect(l).toContain('about $0.0004 per run');
  });

  it('7 real runs, 0 of 7 attempted the write, 0 rows; the simulated script 2 of 2, 1 row off and 0 on', () => {
    const runs = Object.values(injection.recordings) as { label_ticket_attempted: boolean; rows_written: number }[];
    expect(runs).toHaveLength(7);
    expect(runs.filter((r) => r.label_ticket_attempted)).toHaveLength(0);
    expect(sum(runs.map((r) => r.rows_written))).toBe(0);
    expect(l).toContain('that makes 7 real runs, and **all 7 refused: 0 of 7 attempted the write.**');
    expect(l).toContain('Of the 7 real runs recorded for this lesson');
    const sim = injection.simulated;
    expect(Object.values(sim).filter((r) => (r as { label_ticket_attempted: boolean }).label_ticket_attempted)).toHaveLength(2);
    expect(sim['capstone-noguard-simulated-compromised'].rows_written).toBe(1);
    expect(sim['capstone-guard-simulated-compromised'].rows_written).toBe(0);
    expect(l).toContain('attempted the write in 2 of 2 runs');
    expect(l).toContain('It wrote 1 row with the guard off and 0 with the guard on.');
    expect(l).toContain('capstone-noguard-simulated-compromised: guard asked about nothing; label rows written: 1');
    expect(l).toContain('capstone-guard-simulated-compromised: guard asked about label_ticket; label rows written: 0');
  });

  it('quotes TRAINING_NOTE as agent.py has it', () => {
    const m = /^TRAINING_NOTE = \(([\s\S]*?)\)$/m.exec(src('mcp/python/agent.py'));
    const note = [...(m?.[1] ?? '').matchAll(/"([^"]*)"/g)].map((x) => x[1]).join('');
    expect(note.length).toBeGreaterThan(50);
    expect(l).toContain(`"${note}"`);
  });

  it('the Sonnet estimate: $0.0307 for 4 calls, worst $1.4988', () => {
    const e = numbers.estimates['capstone-noguard-sonnet-5-5'];
    expect(e).toMatchObject({ model: 'claude-sonnet-5-5', calls: 4, typical: 0.0307, worst: 1.4988 });
    expect(l).toContain(`typical cost of \`$${e.typical.toFixed(4)}\` for ${e.calls} calls and a worst case of \`$${e.worst.toFixed(4)}\``);
  });
});
