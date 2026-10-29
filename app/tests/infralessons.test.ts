import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { describe, expect, it } from 'vitest';

// The AI Infrastructure track, lessons 1-3 (Task 10 adds 4-6): every number a lesson states in prose or in its quiz
// is pinned here against what produced it: infra/data/derived/capstone.json (replay.py, never written by hand), the
// fixtures (infra/data/fixtures/cases.json and the injector scripts), infra/gateway.yaml, or the source text. Each
// claim is a phrase the lesson must contain plus the check behind it. A last test strips every claimed phrase, the
// lesson references, the numbered lists and the names that carry digits (model ids, ticket ids, status classes) from
// each lesson, and fails on any digit left over, inline code included, so a new number cannot ship unpinned. Fenced
// blocks (the Try-it commands and their printed output) are left out: they were run, and their trace ids, waits and
// wall-clock figures are not pinned.
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '../..');
const lessonsDir = join(here, '../src/content/lessons/infra');
const text = (rel: string) => readFileSync(join(repo, rel), 'utf8');
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const json = (rel: string): any => JSON.parse(text(rel));

const capstone = json('infra/data/derived/capstone.json');
const fixtureConfig = json('infra/data/fixtures/config.json');
const yaml = text('infra/gateway.yaml');
const stagesPy = text('infra/python/stages.py');
const injectorPy = text('infra/python/injector.py');
const gatewayPy = text('infra/python/gateway.py');
type Case = { id: string; config: Record<string, unknown>; upstream: (number | string)[]; state: Record<string, unknown>; expect: { decisions: { stage: string; verdict: string }[]; status: number } };
const cases = json('infra/data/fixtures/cases.json') as Case[];
const kase = (id: string) => cases.find((c) => c.id === id)!;
const derivedCase = (id: string) => (capstone.cases as { id: string; decisions: { stage: string; verdict: string }[]; status: number }[]).find((c) => c.id === id)!;
const verdictOf = (id: string, stage: string) => derivedCase(id).decisions.find((d) => d.stage === stage)?.verdict;
const script = (name: string) => json(`infra/data/fixtures/injector/${name}.json`) as (number | string)[];
type Attempt = { model: string; status: number | null; backoff_ms: number };
const injector = capstone.injector as Record<string, { script: (number | string)[]; status: number; outcome: string; decisions: [string, string][]; attempts: Attempt[] }>;
const hasSection = (src: string, n: number, title: string, mark = '#') => src.includes(`${mark} ── ${n}. ${title}`);
const yamlSection = (n: number) => yaml.split(/^# ── /m).find((s) => s.startsWith(`${n}. `))!;
const cfg = capstone.config;
const retry = cfg.retry as { attempts: number; base_ms: number; max_ms: number };
const haiku = cfg.models['claude-haiku-5-5'] as { rpm: number; tpm: number };
/** The backoff bounds before try n+1 (n from 0), as stages.retry_plan: min(max_ms, base_ms * 2^n) plus [0, base_ms). */
const backoff = (n: number, base = retry.base_ms, max = retry.max_ms) => [Math.min(max, base * 2 ** n), Math.min(max, base * 2 ** n) + base - 1];
const recordingModels = () => {
  const root = join(repo, 'agents/data/recordings');
  const models = new Set<string>();
  let n = 0;
  for (const set of readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory())) {
    for (const f of readdirSync(join(root, set.name)).filter((x) => x.endsWith('.json'))) {
      models.add(JSON.parse(readFileSync(join(root, set.name, f), 'utf8')).request_summary.body.model);
      n++;
    }
  }
  return { models: [...models], n };
};
// The chain's statuses, as run_chain maps refusals: 402 budget, 404 deny, 429 limit, 502 upstream.
const chainReturns = (status: number) => expect(stagesPy).toContain(`return finish(${status}, `);

// Inline code whose digits are names, not numbers, and so need no pin: every other digit in inline code is checked
// like prose. Listed one by one on purpose, so a new span with a digit has to be added here or pinned.
const CODE_OK: RegExp[] = [
  /^claude-(haiku|sonnet|opus)-5-5$/, // model ids
  /^t0\d\d$/, // ticket ids
  /^all_fail_502$/, // a fixture case id
];

// A code quote that appears verbatim in the track's sources is pinned by that source text (the walkthrough spans).
const SOURCES = [
  ...['stages.py', 'gateway.py', 'injector.py', 'config.py', 'replay.py', 'traces.py', 'ledger.py'].map((f) => `infra/python/${f}`),
  ...['stages.go', 'main.go'].map((f) => `infra/go/gateway/${f}`),
  'infra/gateway.yaml',
]
  .map(text)
  .join('\n');
const codeOk = (span: string) => CODE_OK.some((re) => re.test(span)) || SOURCES.includes(span);

const LESSONS = ['01-gateway', '02-routing', '03-resilience'];
const read = (name: string) => readFileSync(join(lessonsDir, `${name}.mdx`), 'utf8');

/** The lesson's prose and quiz: no fenced blocks (they were run; their ids and timings are not pinned), imports or component tags. */
function prose(name: string): string {
  const { content, data } = matter(read(name));
  const quiz = (data.quiz as { question: string; options: string[]; explanation: string }[]).flatMap((q) => [q.question, ...q.options, q.explanation]);
  const body = content
    .replace(/```[\s\S]*?```/g, '')
    .replace(/^import .*$/gm, '')
    .replace(/<[A-Z][^>]*\/>/g, '');
  return [data.title, data.goal, body, ...quiz].join('\n');
}

type Claim = [lesson: string, phrase: string, check: () => void];

const CLAIMS: Claim[] = [
  // ── lesson 1 ──
  ['01-gateway', 'started on port 8791 so the gateway can take 8790', () => {
    expect(cfg.upstream).toBe('http://127.0.0.1:8791');
    expect(gatewayPy).toContain('DEFAULT_PORT = 8790');
  }],
  ['01-gateway', 'The capstone replays all 957 calls the agents track recorded', () => expect([capstone.calls, capstone.all.calls]).toEqual([957, 957])],
  ['01-gateway', 'as three teams: `desk`, `patterns` and `judge`', () => expect(Object.keys(capstone.teams).sort()).toEqual(['desk', 'judge', 'patterns'])],
  ['01-gateway', "the patterns set's 60 calls", () => expect(capstone.sets.patterns.calls).toBe(60)],
  ['01-gateway', 'start the agents replay proxy on port 8791', () => expect(cfg.upstream).toContain(':8791')],
  ['01-gateway', 'start the gateway on 8790', () => expect(gatewayPy).toContain('DEFAULT_PORT = 8790')],
  ['01-gateway', "The bucket's 200,000 tokens a minute run dry, and each call after that would have to wait longer than `max_wait_ms`, 1000 ms, so it gets a 429", () => {
    expect(haiku.tpm).toBe(200_000);
    expect(cfg.limit.max_wait_ms).toBe(1000);
    expect(yaml).toContain('claude-haiku-5-5: {rpm: 60, tpm: 200000, price: lab}');
    chainReturns(429);
  }],
  ['01-gateway', "The desk's model calls go to `http://127.0.0.1:8790` by default", () => {
    expect(text('agents/desk/python/desk_core.py')).toContain('PROXY_URL = "http://127.0.0.1:8790"');
  }],
  ['01-gateway', 'The desk reads every 402 as its proxy', () => {
    expect(text('agents/desk/python/desk_core.py')).toContain('cost cap');
    chainReturns(402);
  }],
  ['01-gateway', 'The budget stage refuses it with a 402 before anything is spent', () => {
    expect(kase('no_team_refused').expect.status).toBe(402);
    expect(derivedCase('no_team_refused').status).toBe(402);
    expect(fixtureConfig.untagged).toBe('refuse');
  }],
  ['01-gateway', 'some calls come back 429', () => chainReturns(429)],
  ['01-gateway', 'Section 1 is the handler', () => expect(hasSection(gatewayPy, 1, 'Handler')).toBe(true)],
  ['01-gateway', 'Section 3 holds the state and books every call', () => expect(hasSection(gatewayPy, 3, 'Server')).toBe(true)],
  // ── lesson 2 ──
  ['02-routing', 'Section 2 of `infra/gateway.yaml` has two lines', () => {
    expect(hasSection(yaml, 2, 'Routes')).toBe(true);
    expect(yamlSection(2).split('\n').filter((l) => l.startsWith('  '))).toHaveLength(2);
  }],
  ['02-routing', "so the capstone's 957 calls all take the first route, and none is denied", () => {
    const { models, n } = recordingModels();
    expect(models).toEqual(['claude-haiku-5-5']);
    expect(n).toBe(951); // the traces replay some recordings twice, so 957 calls (plan P1)
    expect([capstone.calls, capstone.all.refusals]).toEqual([957, 0]);
  }],
  ['02-routing', 'the route stage answers 404, before the limiter or the upstream sees the call', () => {
    expect(derivedCase('unknown_model_deny').status).toBe(404);
    expect(derivedCase('unknown_model_deny').decisions.map((d) => d.stage)).toEqual(['tag', 'budget', 'cache', 'route']);
  }],
  ['02-routing', 'The gateway prices only the models section 1 lists', () => {
    expect(hasSection(yaml, 1, 'Upstream and models')).toBe(true);
    expect(text('infra/python/ledger.py')).toContain('for name, m in cfg.models.items():');
  }],
  ['02-routing', 'the route\'s target fails all three of its tries with 500 and the first fallback answers on the fourth try', () => {
    expect(kase('fallback_after_attempts').upstream).toEqual([500, 500, 500, 200]);
    expect(retry.attempts).toBe(3);
    expect(verdictOf('fallback_after_attempts', 'retry')).toBe('fallback');
  }],
  ['02-routing', 'even though the caller got a 200', () => expect(derivedCase('fallback_after_attempts').status).toBe(200)],
  ['02-routing', 'a call naming `claude-x` stops at the route stage with a 404', () => {
    expect((kase('unknown_model_deny') as unknown as { request: { body: { model: string } } }).request.body.model).toBe('claude-x');
    expect(verdictOf('unknown_model_deny', 'route')).toBe('deny');
    expect(derivedCase('unknown_model_deny').status).toBe(404);
  }],
  ['02-routing', 'so the proxy has no recording and answers 404', () => {
    expect(text('agents/proxy/proxy_handler.py')).toContain('replay_miss');
    expect(text('agents/proxy/proxy_handler.py')).toMatch(/404/);
  }],
  ['02-routing', 'The route stage is section 5 of `infra/python/stages.py`', () => {
    expect(hasSection(stagesPy, 5, 'Route')).toBe(true);
    expect(hasSection(text('infra/go/gateway/stages.go'), 5, 'Route', '//')).toBe(true);
  }],
  ['02-routing', 'the chain in section 8 answers `404`', () => {
    expect(hasSection(stagesPy, 8, 'The chain')).toBe(true);
    expect(stagesPy.split('# ── 8. The chain')[1]).toContain('return finish(404, error_body("not_found_error", r["detail"]))');
  }],
  ['02-routing', 'The original says `1 route(s)`', () => {
    expect(Object.keys(cfg.routes)).toEqual(['claude-haiku-5-5']);
    expect(text('infra/python/config.py')).toContain('route(s)');
  }],
  ['02-routing', 'its scripted upstream answers 500 three times, then 200', () => {
    expect(read('02-routing')).toContain("'upstream': [500, 500, 500, 200]");
    expect(retry.attempts).toBe(3);
  }],
  ['02-routing', 'The chain stops at the route stage with a 404', () => chainReturns(404)],
  ['02-routing', 'The route stage refuses it with a 404 before the limiter or the upstream sees it', () => {
    expect(cfg.deny_unknown).toBe(true);
    expect(derivedCase('unknown_model_deny').status).toBe(404);
  }],
  ['02-routing', 'the upstream answers 500, 500, 500 and then 200', () => expect(kase('fallback_after_attempts').upstream).toEqual([500, 500, 500, 200])],
  ['02-routing', 'the first fallback answered on the fourth, so the caller gets the 200', () => {
    expect(derivedCase('fallback_after_attempts').status).toBe(200);
    expect(retry.attempts + 1).toBe(4);
  }],
  ['02-routing', 'fail, with a 502, because the route', () => chainReturns(502)],
  ['02-routing', 'and make section 2 read:', () => expect(hasSection(yaml, 2, 'Routes')).toBe(true)],
  ['02-routing', 'ok, because the last answer was a 200', () => expect(stagesPy).toContain('verdict = "ok" if len(attempts) == 1 else')],
  // ── lesson 3 ──
  ['03-resilience', 'The upstream answers 429 when the caller is over its', () => {
    expect(stagesPy).toContain('up.status == 429 or up.status >= 500');
  }],
  ['03-resilience', 'is `min(max_ms, base_ms * 2^n)` plus a random jitter between 0 and `base_ms`', () => {
    expect(stagesPy).toContain('return min(cfg.retry.max_ms, cfg.retry.base_ms * 2 ** attempt) + rng.randrange(cfg.retry.base_ms)');
  }],
  ['03-resilience', 'Section 4 of `infra/gateway.yaml` sets `attempts: 3`, `base_ms: 200` and `max_ms: 2000`', () => {
    expect(hasSection(yaml, 4, 'Retry and limits')).toBe(true);
    expect(yaml).toContain('retry: {attempts: 3, base_ms: 200, max_ms: 2000}');
    expect(retry).toEqual({ attempts: 3, base_ms: 200, max_ms: 2000 });
  }],
  ['03-resilience', 'The wait after the first failure is between 200 and 399 ms, after the second between 400 and 599 ms', () => {
    expect([backoff(0), backoff(1)]).toEqual([[200, 399], [400, 599]]);
  }],
  ['03-resilience', 'doubling would first pass 2000 ms after the fifth failed try', () => {
    const first = [0, 1, 2, 3, 4, 5].find((n) => retry.base_ms * 2 ** n > retry.max_ms);
    expect(first).toBe(4); // try n counts from 0, so n = 4 is the fifth failed try
    expect(retry.attempts).toBeLessThan(first! + 1);
  }],
  ['03-resilience', 'Only a 429, a 5xx or a dropped connection is retried', () => expect(stagesPy).toContain('if not (up.error or up.status == 429 or up.status >= 500):')],
  ['03-resilience', 'Any other answer goes back to the caller as it came, a 400 included', () => expect(stagesPy).toContain('if not (up.error or up.status == 429 or up.status >= 500):')],
  ['03-resilience', 'A 404 is never retried either', () => expect(stagesPy).toContain('if up.error is None and up.status == 404:')],
  ['03-resilience', 'When every target has failed, the caller gets a 502', () => {
    chainReturns(502);
    expect(derivedCase('all_fail_502').status).toBe(502);
  }],
  ['03-resilience', '`retry_then_ok`: the upstream answers 429, then 200. The verdict is `retried`, the status 200.', () => {
    expect(kase('retry_then_ok').upstream).toEqual([429, 200]);
    expect([verdictOf('retry_then_ok', 'retry'), derivedCase('retry_then_ok').status]).toEqual(['retried', 200]);
  }],
  ['03-resilience', 'the first connection drops, the second try gets a 200', () => {
    expect(kase('dropped_connection_retry').upstream).toEqual(['drop', 200]);
    expect([verdictOf('dropped_connection_retry', 'retry'), derivedCase('dropped_connection_retry').status]).toEqual(['retried', 200]);
  }],
  ['03-resilience', 'three tries, three 500s. The verdict is `fail`, the status 502.', () => {
    expect(kase('all_fail_502').upstream).toEqual([500, 500, 500]);
    expect([verdictOf('all_fail_502', 'retry'), derivedCase('all_fail_502').status]).toEqual(['fail', 502]);
  }],
  ['03-resilience', 'three 500s from the target, then a 200 from the fallback', () => {
    expect(kase('fallback_after_attempts').upstream).toEqual([500, 500, 500, 200]);
    expect(verdictOf('fallback_after_attempts', 'retry')).toBe('fallback');
  }],
  ['03-resilience', 'Section 1 of `gateway.yaml` gives `claude-haiku-5-5` 60 requests and 200,000 tokens a minute', () => {
    expect(hasSection(yaml, 1, 'Upstream and models')).toBe(true);
    expect([haiku.rpm, haiku.tpm]).toEqual([60, 200_000]);
  }],
  ['03-resilience', 'If that fits within `max_wait_ms`, 1000 ms in the course\'s config', () => {
    expect(yaml).toContain('limit: {max_wait_ms: 1000}');
    expect(cfg.limit.max_wait_ms).toBe(1000);
  }],
  ['03-resilience', 'Otherwise it is refused with a 429 and a `retry-after` header', () => {
    expect(stagesPy).toContain('return finish(429, error_body("rate_limit_error", d["detail"]), {"retry-after": after})');
  }],
  ['03-resilience', 'At 60 a minute, one request refills in exactly 1000 ms, which is within `max_wait_ms`, so the call waits', () => {
    expect((1 / (haiku.rpm / 60)) * 1000).toBe(cfg.limit.max_wait_ms);
    expect(kase('limit_wait').state).toEqual({ bucket: { 'claude-haiku-5-5': { tokens: 200000, requests: 0 } } });
    expect(verdictOf('limit_wait', 'limit')).toBe('wait');
  }],
  ['03-resilience', '`limit_refuse` is the same call with `max_wait_ms` at 500: the wait no longer fits, and the answer is a 429', () => {
    expect(kase('limit_refuse').config).toEqual({ limit: { max_wait_ms: 500 } });
    expect(kase('limit_refuse').state).toEqual(kase('limit_wait').state);
    expect([verdictOf('limit_refuse', 'limit'), derivedCase('limit_refuse').status]).toEqual(['refuse', 429]);
  }],
  ['03-resilience', "the gateway's clock starts at 0 and moves by each call's recorded wall clock", () => {
    expect(capstone.about.time).toContain("the gateway's clock starts at 0 and moves by each call's recorded ms");
  }],
  ['03-resilience', 'Over all 957 calls, that makes 0 retries and 0 refusals.', () => {
    expect([capstone.all.calls, capstone.all.retries, capstone.all.refusals]).toEqual([957, 0, 0]);
  }],
  ['03-resilience', '200 answers a fixed reply, 429 answers with `retry-after: 0`', () => {
    expect(injectorPy).toContain('if step == 200:');
    expect(injectorPy).toContain('return 429, {**JSON, "retry-after": "0"}');
  }],
  ['03-resilience', '- `flaky`: 429, then 500, then 200.', () => expect([script('flaky'), injector.flaky.script]).toEqual([[429, 500, 200], [429, 500, 200]])],
  ['03-resilience', '- `ratelimited`: 429 twice, then 200.', () => expect([script('ratelimited'), injector.ratelimited.script]).toEqual([[429, 429, 200], [429, 429, 200]])],
  ['03-resilience', '- `dead`: 500, and again 500, forever.', () => {
    expect([script('dead'), injector.dead.script]).toEqual([[500], [500]]);
    expect(injectorPy).toContain('step = self.script[self.served % len(self.script)]');
  }],
  ['03-resilience', '`flaky` and `ratelimited` both end in a 200 on the third try', () => {
    for (const s of ['flaky', 'ratelimited']) {
      expect(injector[s].attempts.map((a) => a.status), s).toEqual([...script(s)]);
      expect([injector[s].status, injector[s].outcome], s).toEqual([200, 'ok']);
      expect(injector[s].decisions.find(([st]) => st === 'retry')![1], s).toBe('retried');
    }
  }],
  ['03-resilience', '`dead` uses up all three tries and the caller gets a 502', () => {
    expect(injector.dead.attempts.map((a) => a.status)).toEqual([500, 500, 500]);
    expect([injector.dead.status, injector.dead.outcome]).toEqual([502, 'failed']);
  }],
  ['03-resilience', 'The capstone ran these with `base_ms` set to 1 and a fixed random seed, so the waits read 1 ms and 2 ms', () => {
    expect(capstone.about.injector).toContain('retry.base_ms 1, rng Random(0)');
    for (const s of ['flaky', 'ratelimited', 'dead']) expect(injector[s].attempts.map((a) => a.backoff_ms), s).toEqual([1, 2, 0]);
  }],
  ['03-resilience', "since with `base_ms` at 1 the jitter is always 0. With the course's 200 they would fall between 200 and 399 ms, then between 400 and 599 ms.", () => {
    expect([backoff(0, 1), backoff(1, 1)]).toEqual([[1, 1], [2, 2]]);
    expect([backoff(0), backoff(1)]).toEqual([[200, 399], [400, 599]]);
  }],
  ['03-resilience', 'The limit stage is section 6 of `infra/python/stages.py`', () => expect(hasSection(stagesPy, 6, 'Limit')).toBe(true)],
  ['03-resilience', 'The chain turns that into a 429 with a `retry-after` in whole seconds', () => {
    expect(stagesPy).toContain('after = str(max(1, math.ceil(d["wait_ms"] / 1000)))');
  }],
  ['03-resilience', 'The retry stage is section 7.', () => expect(hasSection(stagesPy, 7, 'Retry and upstream')).toBe(true)],
  ['03-resilience', 'fails at once on a 404, without a retry', () => expect(stagesPy).toContain('answered 404, not retried')],
  ['03-resilience', "The injector's script is section 1 of `infra/python/injector.py`", () => expect(hasSection(injectorPy, 1, 'Script')).toBe(true)],
  ['03-resilience', "The Go twin's sections 6 and 7 make the same decisions on every fixture case", () => {
    const go = text('infra/go/gateway/stages.go');
    expect(hasSection(go, 6, 'Limit', '//') && hasSection(go, 7, 'Retry and upstream', '//')).toBe(true);
    expect(text('infra/python/tests/test_twin.py')).toContain('print the same lines, byte for byte');
  }],
  ['03-resilience', 'The first wait falls between 200 and 399 ms and the second between 400 and 599 ms', () => {
    expect([backoff(0), backoff(1)]).toEqual([[200, 399], [400, 599]]);
  }],
  ['03-resilience', "The caller gets a 502, and the trace's attempts are three 500s.", () => {
    expect(script('dead')).toEqual([500]);
    expect(retry.attempts).toBe(3);
    chainReturns(502);
  }],
  ['03-resilience', 'The upstream answers a request with 400', () => expect(stagesPy).toContain('if not (up.error or up.status == 429 or up.status >= 500):')],
  ['03-resilience', 'It retries three times with backoff, then gives up with a 502', () => chainReturns(502)],
  ['03-resilience', 'A 400 says the request itself is wrong', () => expect(stagesPy).toContain('if not (up.error or up.status == 429 or up.status >= 500):')],
  ['03-resilience', 'It answers 502, because any status that is not 200 is a failure', () => chainReturns(502)],
  ['03-resilience', 'The bucket for a model at 60 requests a minute has no request left, and max_wait_ms is 1000', () => {
    expect([haiku.rpm, cfg.limit.max_wait_ms]).toEqual([60, 1000]);
  }],
  ['03-resilience', 'It refuses the call with a 429 at once', () => chainReturns(429)],
  ['03-resilience', 'It answers 502, because no request is left', () => chainReturns(502)],
  ['03-resilience', 'It waits 1000 ms, the time one request takes to refill, and lets the call through. With max_wait_ms at 500 the same call is refused with a 429', () => {
    expect((1 / (haiku.rpm / 60)) * 1000).toBe(1000);
    expect([verdictOf('limit_wait', 'limit'), verdictOf('limit_refuse', 'limit')]).toEqual(['wait', 'refuse']);
    expect(kase('limit_refuse').config).toEqual({ limit: { max_wait_ms: 500 } });
  }],
  ['03-resilience', 'refuses with a 429 and a retry-after when it does not', () => chainReturns(429)],
  ['03-resilience', 'The wait before the next try after failed try `n`, counting from 0,', () => {
    expect(stagesPy).toContain('Backoff in ms before the next try after failed attempt number `attempt` (from 0)');
  }],
  ['03-resilience', 'When a 429 carries a `retry-after` header, the stage waits at least that long', () => {
    expect(stagesPy).toContain('wait = max(retry_plan(cfg, n, state.rng), retry_after_ms(up.headers))');
  }],
  ['03-resilience', 'how the verdict changes when the script never answers 200', () => expect(script('dead')).not.toContain(200)],
  ['03-resilience', 'sends a 429 that asks for no wait', () => expect(injectorPy).toContain('return 429, {**JSON, "retry-after": "0"}')],
  ['03-resilience', "The caller got a 200 and the injector's reply", () => {
    expect(injectorPy).toContain('if step == 200:');
    expect(script('flaky').at(-1)).toBe(200);
  }],
  ['03-resilience', 'It hands the 400 back at once', () => expect(stagesPy).toContain('if not (up.error or up.status == 429 or up.status >= 500):  # answered: no retry')],
];

describe('infra lessons: files, aids and anatomy', () => {
  it('has lessons 1-3 (Task 10 adds 4-6), each numbered by its file name', () => {
    const files = readdirSync(lessonsDir).filter((f) => f.endsWith('.mdx')).sort();
    expect(files.slice(0, 3)).toEqual(LESSONS.map((l) => `${l}.mdx`));
    for (const [i, l] of LESSONS.entries()) {
      const fm = matter(read(l)).data;
      expect([fm.track, fm.order, fm.slug], l).toEqual(['infra', i + 1, l.replace(/^\d+-/, '')]);
    }
    expect(matter(read('01-gateway')).data.title).toBe('What an AI gateway is');
    expect(matter(read('02-routing')).data.title).toBe('Routing and fallback');
    expect(matter(read('03-resilience')).data.title).toBe('Retries and rate limits');
  });

  it.each([
    ['01-gateway', ['Concept', 'See it', 'The code', 'Try it']],
    ['02-routing', ['Concept', 'See it', 'The code', 'How Go does it', 'Try it']],
    ['03-resilience', ['Concept', 'See it', 'The code', 'How Go does it', 'Try it']],
  ] as const)('%s has the H2s in order', (l, h2s) => {
    expect([...read(l).matchAll(/^## (.+)$/gm)].map((m) => m[1])).toEqual([...h2s]);
  });

  it('puts the aid spec §6 names for each lesson inside its See it, and quotes the code the plan names', () => {
    const seeIt = (l: string) => read(l).split('\n## See it\n')[1].split('\n## ')[0];
    expect(seeIt('01-gateway')).toContain('<FlowDiagram diagram="infra-gateway" />');
    expect(seeIt('02-routing')).toContain('<RouteTable />');
    expect(seeIt('03-resilience')).toContain('<RetryTimeline initial="flaky" />');
    const l1 = read('01-gateway');
    for (const s of ['<CodeTabs source="infra-gateway" section={1} pythonOnly />', '<CodeTabs source="infra-gateway" section={3} pythonOnly />']) expect(l1).toContain(s);
    expect(read('02-routing')).toContain('<CodeTabs source="infra-stages" section={5} goSection={5} />');
    const l3 = read('03-resilience');
    for (const s of [
      '<CodeTabs source="infra-stages" section={6} goSection={6} />',
      '<CodeTabs source="infra-stages" section={7} goSection={7} />',
      '<CodeTabs source="infra-injector" section={1} pythonOnly />',
    ]) expect(l3).toContain(s);
  });

  it('gives lesson 1, which has no twin section, the Go callout, and lessons 2 and 3 the Go section instead', () => {
    expect(read('01-gateway')).toContain('<Callout kind="go" title="The Go twin starts in lesson 2">');
    for (const l of ['02-routing', '03-resilience']) expect(read(l), l).not.toContain('<Callout kind="go"');
  });

  it('lesson 1 carries the concept tour: the five jobs, the drop-in trade and the out-of-scope list as what a production gateway adds', () => {
    const l1 = read('01-gateway');
    for (const s of ['**Routing with fallback.**', '**Retries and rate limits.**', '**A <Term t="response cache" />.**', '**Budgets with a spend ledger.**', '**Traces.**']) expect(l1).toContain(s);
    expect(l1).toContain('### The drop-in trade');
    expect(l1).toContain('So headers are the only channel.');
    const adds = l1.split('### What a production gateway adds')[1].split('\n## ')[0];
    // spec §2: the out-of-scope list, named as what a production gateway adds
    for (const item of ['Multi-tenant auth and key issuance', 'Streaming beyond byte forwarding', 'Distributed state', 'A metrics backend and dashboards', 'Horizontal deployment', 'Serving models']) {
      expect(adds).toContain(`**${item}.**`);
    }
    expect(adds.match(/^- \*\*/gm)).toHaveLength(6);
    expect(l1).toContain('Six things are left out on purpose');
  });

  it('lesson 2 states the rule: bodies are never edited, with the replay key, the cache key and audit', () => {
    const l2 = read('02-routing');
    expect(l2).toContain('### Bodies are never edited');
    expect(l2).toContain("method, path and body: the replay key");
    expect(l2).toContain('a gateway that edits requests breaks replay, caching and audit alike');
    expect(stagesPy).toContain('Bodies go upstream byte for byte; a fallback changes where, never what.');
    expect(stagesPy).toContain('The key hashes the path and the body bytes.');
  });

  it('states ruling I6 where the limiter meets a clock: wall clock by hand, replay time in the capstone', () => {
    expect(read('01-gateway')).toContain('A gateway you start by hand judges the limiter on the wall clock.');
    expect(read('03-resilience')).toContain('The capstone runs on replay time instead');
    expect(capstone.about.time).toMatch(/^replay time:/);
  });

  it('never runs a paid or live step and never names a key in a Try it', () => {
    for (const l of LESSONS) {
      const t = read(l);
      expect(t, l).not.toMatch(/--live|--yes|--mode record|ANTHROPIC_API_KEY|curl /);
    }
  });

  it('shows how to stop every server a Try it starts', () => {
    for (const l of ['01-gateway', '03-resilience']) expect(read(l).split('\n## Try it\n')[1], l).toContain('with Ctrl-C');
  });

  it('gives each lesson 3 quiz questions of 4 options in plain text', () => {
    for (const l of LESSONS) {
      const quiz = matter(read(l)).data.quiz as { question: string; options: string[]; explanation: string }[];
      expect(quiz, l).toHaveLength(3);
      for (const q of quiz) {
        expect(q.options, l).toHaveLength(4);
        expect([q.question, ...q.options, q.explanation].join(' '), l).not.toContain('`');
      }
    }
  });

  it('every file named in the lessons exists', () => {
    for (const l of LESSONS) {
      for (const [, p] of read(l).matchAll(/`((?:infra|agents|llms)\/[\w./-]+\.(?:py|go|yaml|json|jsonl))`/g)) {
        if (p.startsWith('infra/data/traces/') || p === 'infra/data/ledger.jsonl' || p === 'infra/data/cache.json') continue; // run outputs
        expect(existsSync(join(repo, p)), `${l} names ${p}`).toBe(true);
      }
    }
  });
});

describe('infra lessons: every number is pinned', () => {
  it.each(CLAIMS.map((c) => [`${c[0]}: ${c[1].slice(0, 70)}`, c] as const))('%s', (_n, [lesson, phrase, check]) => {
    expect(prose(lesson), `${lesson} no longer says: ${phrase}`).toContain(phrase);
    check();
  });

  it.each(LESSONS)('%s: no digit is left once the claims, the lesson references, the numbered lists and the names are taken out', (lesson) => {
    let t = prose(lesson);
    for (const [l, phrase] of CLAIMS) if (l === lesson) t = t.split(phrase).join(' ');
    const refs = [...t.matchAll(/\b[Ll]esson (\d)\b/g)].map((m) => Number(m[1]));
    expect(refs.every((n) => n >= 1 && n <= 6), `${lesson} refers to ${refs}`).toBe(true);
    t = t
      .replace(/\b[Ll]esson \d\b/g, ' ')
      .replace(/^\d\. /gm, ' ')
      .replace(/`([^`]*)`/g, (_m, span: string) => (codeOk(span) ? ' ' : ` ${span} `))
      .replace(/\bclaude-(haiku|sonnet|opus)-5-5\b/g, ' ') // model ids
      .replace(/\bt0\d\d\b/g, ' ') // ticket ids
      .replace(/\b[45]xx\b/g, ' '); // status classes
    expect(t.match(/.{0,40}\d.{0,20}/g) ?? [], `${lesson}: an unpinned number`).toEqual([]);
  });
});
