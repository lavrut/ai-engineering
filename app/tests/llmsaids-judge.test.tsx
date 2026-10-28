// @vitest-environment jsdom
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import {
  RUBRIC_IDS,
  buildLlmsData,
  cohenKappa,
  loadLlmsData,
  parseJsonl,
  positionFlips,
  type LlmsData,
  type Prompts,
  type RubricId,
} from '../src/lib/llmsData';
import { judgeKey } from '../src/lib/llmsKey';
import {
  DEFAULT_JUDGE_GROUPS,
  JUDGE_MODEL,
  LABELS_EVENT,
  LABELS_KEY,
  RECORD_COMMAND,
  agreementProps,
  agreementStats,
  judgeCardProps,
  labelingProps,
  labelsJsonl,
  mergeLabels,
  parseStoredLabels,
  readStoredLabels,
  replyFingerprint,
  writeStoredLabels,
  type JudgeKeyFn,
  type StoredLabels,
} from '../src/lib/llmsAids';
import JudgeCard from '../src/components/llms/JudgeCard';
import AgreementView from '../src/components/llms/AgreementView';
import LabelingPage from '../src/components/llms/LabelingPage';

// Fixtures are read with node:fs (ruling R6); the app loader never sees them.
const here = dirname(fileURLToPath(import.meta.url));
const fixtures = join(here, '../../llms/data/fixtures');
const read = (p: string) => readFileSync(join(fixtures, p), 'utf8');
const fixtureResponses = () => {
  const dir = join(fixtures, 'responses');
  return Object.fromEntries(readdirSync(dir).map((f) => [`responses/${f}`, readFileSync(join(dir, f), 'utf8')]));
};
function fixtureData(responses: Record<string, string> = fixtureResponses()): LlmsData {
  return buildLlmsData({ tickets: read('tickets.jsonl'), labels: read('reply_labels.jsonl'), prompts: read('prompts.json'), responses });
}
const keyFor = (prompts: Prompts): JudgeKeyFn => (ticket, reply, order) => judgeKey(ticket, reply, order, JUDGE_MODEL, prompts);

const AFTER = 'responses/judge__claude-opus-5-5__after__default.jsonl';
const labels = (a: boolean, t: boolean, n: boolean): Record<RubricId, boolean> => ({ addresses_issue: a, tone: t, no_false_promises: n });

/**
 * The fixture data plus two judged v2_rules test replies (f005 graded, f008 an error), keyed like the lab.
 * v1_basic has no reply for f008 (its triage call failed), so nothing of v1 is judged here.
 */
function fixtureWithTestReplies(): LlmsData {
  const base = fixtureData();
  const ticket = (id: string) => base.tickets.find((t) => t.id === id)!.text;
  const reply = (variant: string, id: string) => {
    const run = base.runs.find((r) => r.config.kind === 'triage' && r.config.prompt_version === variant)!;
    return (run.lines.find((l) => l.case_id === id && l.config.rep === 0)!.output as { reply: string }).reply;
  };
  const config = { kind: 'judge', model: JUDGE_MODEL, effort: null, prompt_version: 'after', rep: 0 };
  const verdict = (pass: boolean, why: string) => ({ pass, reason: why });
  const extra = [
    {
      key: keyFor(base.prompts)(ticket('f005'), reply('v2_rules', 'f005'), 'after'),
      config,
      case_id: 'f005',
      output: { addresses_issue: verdict(true, 'Points to the invoice.'), tone: verdict(true, 'Friendly.'), no_false_promises: verdict(false, 'Promises a date.') },
      stop_reason: 'end_turn',
      input_tokens: 10,
      output_tokens: 10,
      latency_ms: 5,
      error: null,
    },
    {
      key: keyFor(base.prompts)(ticket('f008'), reply('v2_rules', 'f008'), 'after'),
      config,
      case_id: 'f008',
      output: null,
      stop_reason: null,
      input_tokens: 0,
      output_tokens: 0,
      latency_ms: 5,
      error: 'Connection error.',
    },
  ];
  const responses = fixtureResponses();
  responses[AFTER] += extra.map((l) => JSON.stringify(l)).join('\n') + '\n';
  return fixtureData(responses);
}

// Node 26 has its own (file-less, undefined) global localStorage that shadows
// jsdom's, so each test gets a fresh Map-backed one on window.
function installStorage() {
  const map = new Map<string, string>();
  const storage = {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, String(v)),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
  };
  Object.defineProperty(window, 'localStorage', { configurable: true, get: () => storage });
}
beforeEach(installStorage);
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── Labels: storage, merge and export ───────────────────────────────────────
describe('reply labels: storage, merge, export', () => {
  const props = labelingProps(fixtureData());

  it('labelingProps passes each reply with its ticket text, the drafted labels and a fingerprint', () => {
    expect(props.cases).toHaveLength(4);
    expect(props.cases[0]).toEqual({
      id: 'r001',
      ticket_id: 'f001',
      ticket: '[fixture] I was charged twice this month, please check.',
      reply: 'Sorry about the double charge. I have refunded the duplicate, you will see it by tomorrow.',
      labels: labels(true, true, false),
      fp: replyFingerprint('Sorry about the double charge. I have refunded the duplicate, you will see it by tomorrow.'),
    });
    expect(props.rubric.map((r) => r.id)).toEqual([...RUBRIC_IDS]);
  });

  it('replyFingerprint is stable and differs when the reply changes', () => {
    expect(replyFingerprint('abc')).toBe(replyFingerprint('abc'));
    expect(replyFingerprint('abc')).not.toBe(replyFingerprint('abd'));
    expect(replyFingerprint('abc')).toMatch(/^[0-9a-f]{8}$/);
  });

  it('stored labels override the file, but only for the same reply text', () => {
    const stored: StoredLabels = {
      r002: { fp: props.cases[1].fp, labels: labels(false, false, false) },
      r003: { fp: 'deadbeef', labels: labels(true, true, true) }, // stale: the reply changed since
    };
    const m = mergeLabels(props.cases, stored);
    expect(m.mine).toEqual([false, true, false, false]);
    expect(m.labels[0]).toEqual(props.cases[0].labels);
    expect(m.labels[1]).toEqual(labels(false, false, false));
    expect(m.labels[2]).toEqual(props.cases[2].labels);
  });

  it('parseStoredLabels ignores garbage and incomplete entries', () => {
    expect(parseStoredLabels(null)).toEqual({});
    expect(parseStoredLabels('not json')).toEqual({});
    expect(parseStoredLabels('[1,2]')).toEqual({});
    const raw = JSON.stringify({
      v: 1,
      replies: {
        r001: { fp: 'aaaaaaaa', labels: labels(true, false, true) },
        r002: { fp: 'bbbbbbbb', labels: { addresses_issue: true, tone: 'yes', no_false_promises: true } },
        r003: { labels: labels(true, true, true) },
      },
    });
    expect(parseStoredLabels(raw)).toEqual({ r001: { fp: 'aaaaaaaa', labels: labels(true, false, true) } });
  });

  it('read and write go through storage and survive a storage that throws', () => {
    const map = new Map<string, string>();
    const fake = { getItem: (k: string) => map.get(k) ?? null, setItem: (k: string, v: string) => void map.set(k, v) };
    const stored: StoredLabels = { r001: { fp: props.cases[0].fp, labels: labels(false, true, true) } };
    expect(writeStoredLabels(stored, fake)).toBe(true);
    expect(JSON.parse(map.get(LABELS_KEY)!)).toEqual({ v: 1, replies: stored });
    expect(readStoredLabels(fake)).toEqual({ stored, available: true });

    const broken = {
      getItem: () => {
        throw new Error('SecurityError');
      },
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    };
    expect(readStoredLabels(broken)).toEqual({ stored: {}, available: false });
    expect(writeStoredLabels(stored, broken)).toBe(false);
    expect(readStoredLabels(null)).toEqual({ stored: {}, available: false });
  });

  it('labelsJsonl with the drafted labels reproduces reply_labels.jsonl byte for byte', () => {
    expect(labelsJsonl(props.cases, props.cases.map((c) => c.labels))).toBe(read('reply_labels.jsonl'));
    const realText = readFileSync(join(here, '../../llms/data/reply_labels.jsonl'), 'utf8');
    const real = labelingProps(loadLlmsData());
    expect(labelsJsonl(real.cases, real.cases.map((c) => c.labels))).toBe(realText);
  });

  it('labelsJsonl writes the Python line shape: key order, separators, escapes, non-ASCII kept', () => {
    // python3 -c 'import json; print(json.dumps({"id":"r099","ticket_id":"t001",
    //   "reply":"Say \"hi\"\nnow — café\t\\ </b> \u0001","labels":{...}}, ensure_ascii=False))'
    const line = labelsJsonl(
      [{ id: 'r099', ticket_id: 't001', reply: 'Say "hi"\nnow — café\t\\ </b> \u0001' }],
      [labels(false, true, true)],
    );
    expect(line).toBe(
      '{"id": "r099", "ticket_id": "t001", "reply": "Say \\"hi\\"\\nnow — café\\t\\\\ </b> \\u0001", "labels": {"addresses_issue": false, "tone": true, "no_false_promises": true}}\n',
    );
  });

  it('an edited export parses back to the edits, one line per reply, in file order', () => {
    const edited = props.cases.map((c) => c.labels);
    edited[2] = labels(true, true, false);
    const out = labelsJsonl(props.cases, edited);
    const lines = parseJsonl<{ id: string; ticket_id: string; reply: string; labels: Record<string, boolean> }>(out, 'export');
    expect(lines.map((l) => l.id)).toEqual(['r001', 'r002', 'r003', 'r004']);
    expect(Object.keys(lines[0])).toEqual(['id', 'ticket_id', 'reply', 'labels']);
    expect(Object.keys(lines[0].labels)).toEqual([...RUBRIC_IDS]);
    expect(lines[2].labels).toEqual(labels(true, true, false));
    expect(lines[2].reply).toBe(props.cases[2].reply);
  });
});

// ── Judge verdicts ──────────────────────────────────────────────────────────
describe('judge card data', () => {
  it('lists labelled replies in both orders and judged test replies, selected by key', () => {
    const data = fixtureWithTestReplies();
    const p = judgeCardProps(data, keyFor(data.prompts));
    expect(p.recorded).toBe(true);
    expect(p.cases.map((c) => c.value)).toEqual(['r001', 'r002', 'r003', 'r004', 'v2_rules:f005', 'v2_rules:f008']);
    const r001 = p.cases[0];
    expect(r001).toMatchObject({ group: 'labelled', caseId: 'r001', ticketId: 'f001' });
    expect(Object.keys(r001.orders).sort()).toEqual(['after', 'before']);
    expect(r001.orders.after).toHaveLength(3);
    expect(p.tickets.f001).toBe('[fixture] I was charged twice this month, please check.');
    const graded = p.cases.find((c) => c.value === 'v2_rules:f005')!;
    expect(graded.reply).toBe('You can download the May invoice under Settings > Billing.');
    expect(graded.orders).toEqual({
      after: [
        { pass: true, reason: 'Points to the invoice.' },
        { pass: true, reason: 'Friendly.' },
        { pass: false, reason: 'Promises a date.' },
      ],
    });
    expect(p.cases.find((c) => c.value === 'v2_rules:f008')!.orders).toEqual({ after: { notGraded: 'Connection error.' } });
  });

  it('groups keeps only the chosen kinds of case, and initial falls back to the first case', () => {
    const data = fixtureWithTestReplies();
    const p = judgeCardProps(data, keyFor(data.prompts), { groups: ['v2_rules'], initial: 'r001' });
    expect(p.cases.map((c) => c.value)).toEqual(['v2_rules:f005', 'v2_rules:f008']);
    expect(p.initial).toBe('v2_rules:f005');
    expect(Object.keys(p.tickets)).toEqual(['f005', 'f008']);
    expect(judgeCardProps(data, keyFor(data.prompts), { initial: 'r003' }).initial).toBe('r003');
  });

  it('is not recorded with no responses', () => {
    const data = fixtureData({});
    expect(judgeCardProps(data, keyFor(data.prompts))).toMatchObject({ recorded: false, cases: [] });
  });
});

describe('agreement data', () => {
  const data = fixtureData();
  const p = agreementProps(data, keyFor(data.prompts));
  const human = p.cases.map((c) => c.file);

  it('passes per reply: fingerprint, drafted labels, both orders verdicts', () => {
    expect(p.recorded).toBe(true);
    expect(p.cases).toHaveLength(4);
    expect(Object.keys(p.cases[0]).sort()).toEqual(['after', 'before', 'file', 'fp', 'id']);
    expect(p.cases[0].fp).toBe(replyFingerprint(data.labels[0].reply));
  });

  it('agreementStats matches expected_scores.json (rubric after the reply) and the position flips', () => {
    const expected = JSON.parse(read('expected_scores.json'));
    const stats = agreementStats(p.cases, human, 'after');
    for (const s of stats) {
      expect(s.agreement).toBeCloseTo(expected.agreement[s.id], 10);
      expect(s.kappa).toBeCloseTo(expected.kappa[s.id], 10);
      expect(s.flips).toBe(expected.position_flips[s.id]);
    }
    const ai = stats.find((s) => s.id === 'addresses_issue')!;
    expect(ai.kappa).toBe(cohenKappa(p.cases.map((c) => !!c.after[0]), human.map((h) => h[0])));
  });

  it('is not recorded when a labelled reply has no judge line in one order', () => {
    const responses = fixtureResponses();
    delete responses['responses/judge__claude-opus-5-5__before__default.jsonl'];
    const d = fixtureData(responses);
    expect(agreementProps(d, keyFor(d.prompts)).recorded).toBe(false);
  });
});

// ── JudgeCard ───────────────────────────────────────────────────────────────
describe('JudgeCard', () => {
  const data = fixtureWithTestReplies();
  const props = judgeCardProps(data, keyFor(data.prompts));

  it('shows the ticket, the reply and a pass/fail chip with a reason per criterion', () => {
    render(<JudgeCard {...props} />);
    expect(screen.getByText('[fixture] I was charged twice this month, please check.')).toBeTruthy();
    expect(screen.getByText(/I have refunded the duplicate/)).toBeTruthy();
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
    // Text, not only colour, says pass or fail.
    for (const li of items) expect(li.textContent).toMatch(/^(✓ pass|✗ fail)/);
    expect(items[0].textContent).toContain('Hand-written fixture verdict for addresses_issue.');
  });

  it('the picker switches case, including a test reply and one the judge could not grade', () => {
    render(<JudgeCard {...props} />);
    const picker = screen.getByLabelText('Judged reply') as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: 'v2_rules:f005' } });
    expect(screen.getByText('You can download the May invoice under Settings > Billing.')).toBeTruthy();
    expect(screen.getByText('Promises a date.')).toBeTruthy();
    expect(screen.queryByRole('radiogroup')).toBeNull(); // only the after order was judged
    fireEvent.change(picker, { target: { value: 'v2_rules:f008' } });
    expect(screen.getByText(/Not graded/)).toBeTruthy();
    expect(screen.getByText(/Connection error\./)).toBeTruthy();
  });

  it('a labelled reply can be shown with the rubric before the reply', () => {
    render(<JudgeCard {...props} />);
    fireEvent.change(screen.getByLabelText('Judged reply'), { target: { value: 'r002' } });
    const after = screen.getAllByRole('listitem').map((li) => li.textContent);
    fireEvent.click(screen.getByLabelText('Rubric before the reply'));
    const before = screen.getAllByRole('listitem').map((li) => li.textContent);
    // r002's addresses_issue verdict flips between the orders in the fixture.
    const r = props.cases[1].orders as { before: { pass: boolean }[]; after: { pass: boolean }[] };
    expect(r.before[0].pass).not.toBe(r.after[0].pass);
    expect(before[0]).not.toBe(after[0]);
    expect(before[0]).toMatch(/changes with the order/);
  });

  it('shows the record command with no recorded run', () => {
    const d = fixtureData({});
    render(<JudgeCard {...judgeCardProps(d, keyFor(d.prompts))} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
    expect(screen.getByText(RECORD_COMMAND)).toBeTruthy();
  });
});

// ── AgreementView ───────────────────────────────────────────────────────────
describe('AgreementView', () => {
  const data = fixtureData();
  const props = agreementProps(data, keyFor(data.prompts));
  const human = props.cases.map((c) => c.file);
  const flipped = props.cases.filter((c) => c.before.some((v, i) => v !== c.after[i])).map((c) => c.id);

  const statRow = (id: string) => screen.getByRole('row', { name: new RegExp(`^${id}`) });

  it('shows agreement and kappa per criterion, kappa equal to cohenKappa', () => {
    render(<AgreementView {...props} />);
    for (const [i, id] of RUBRIC_IDS.entries()) {
      const k = cohenKappa(props.cases.map((c) => !!c.after[i]), human.map((h) => h[i]));
      const row = statRow(id);
      expect(within(row).getByText(k.toFixed(2))).toBeTruthy();
    }
    expect(within(statRow('addresses_issue')).getByText('75.0%')).toBeTruthy();
    expect(screen.getByText('Human labels: the committed labels in reply_labels.jsonl (drafted by Claude, reviewed by a person on 2026-10-28).')).toBeTruthy();
  });

  it('Swap order shows the before run and changes only the flipped rows; flipped rows are marked in text', () => {
    render(<AgreementView {...props} />);
    fireEvent.click(screen.getByLabelText(/Only rows where/)); // show every reply
    const verdictRow = (id: string) => screen.getByRole('row', { name: new RegExp(`^Reply ${id}`) });
    const snap = () => Object.fromEntries(props.cases.map((c) => [c.id, verdictRow(c.id).textContent]));
    const after = snap();
    expect(screen.getByText(/Rubric after the reply/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Swap order' }));
    expect(screen.getByText(/Rubric before the reply/)).toBeTruthy();
    const before = snap();
    const changed = props.cases.filter((c) => after[c.id] !== before[c.id]).map((c) => c.id);
    expect(changed).toEqual(flipped);
    expect(flipped).toEqual(['r001', 'r002', 'r004']);
    for (const c of props.cases) {
      const row = verdictRow(c.id);
      expect(row.classList.contains('is-flipped')).toBe(flipped.includes(c.id));
      expect(/flips/.test(row.textContent ?? '')).toBe(flipped.includes(c.id));
    }
    // The before order's kappa for every criterion is cohenKappa of the before verdicts.
    for (const [i, id] of RUBRIC_IDS.entries()) {
      const k = cohenKappa(props.cases.map((c) => !!c.before[i]), human.map((h) => h[i]));
      expect(within(statRow(id)).getByText(k.toFixed(2))).toBeTruthy();
    }
    expect(positionFlips(props.cases.map((c) => c.before[0]), props.cases.map((c) => c.after[0]))).toBe(2);
  });

  it('uses the learner labels from browser storage when present, and says so', () => {
    const stored: StoredLabels = { r001: { fp: props.cases[0].fp, labels: labels(false, true, true) } };
    window.localStorage.setItem(LABELS_KEY, JSON.stringify({ v: 1, replies: stored }));
    render(<AgreementView {...props} />);
    expect(screen.getByText(/your labels from this browser for 1 reply/)).toBeTruthy();
    const merged = mergeLabels(props.cases.map((c) => ({ id: c.id, fp: c.fp, labels: Object.fromEntries(RUBRIC_IDS.map((r, i) => [r, c.file[i]])) as Record<RubricId, boolean> })), stored);
    const k = cohenKappa(props.cases.map((c) => !!c.after[0]), merged.labels.map((l) => l.addresses_issue));
    expect(within(statRow('addresses_issue')).getByText(k.toFixed(2))).toBeTruthy();
  });

  it('follows label edits made on the same page', () => {
    render(<AgreementView {...props} />);
    const stored: StoredLabels = { r001: { fp: props.cases[0].fp, labels: labels(false, true, true) } };
    act(() => {
      window.dispatchEvent(new CustomEvent(LABELS_EVENT, { detail: stored }));
    });
    expect(screen.getByText(/your labels from this browser for 1 reply/)).toBeTruthy();
  });

  it('shows the record command with no recorded run', () => {
    const d = fixtureData({});
    render(<AgreementView {...agreementProps(d, keyFor(d.prompts))} />);
    expect(screen.getByText('No recorded run yet')).toBeTruthy();
  });
});

// ── LabelingPage ────────────────────────────────────────────────────────────
describe('LabelingPage', () => {
  const props = labelingProps(fixtureData());
  const progress = () => screen.getByText(/of 4 reviewed/).textContent;
  const stored = () => parseStoredLabels(window.localStorage.getItem(LABELS_KEY));

  it('shows one reply at a time with its ticket and the drafted labels', () => {
    render(<LabelingPage {...props} />);
    expect(screen.getByText('[fixture] I was charged twice this month, please check.')).toBeTruthy();
    expect(screen.getByText(/I have refunded the duplicate/)).toBeTruthy();
    expect(screen.getByText(/Reply 1 of 4/)).toBeTruthy();
    const group = screen.getByRole('radiogroup', { name: /no_false_promises/ });
    expect((within(group).getByRole('radio', { name: 'Fail' }) as HTMLInputElement).checked).toBe(true);
    expect(progress()).toBe('0 of 4 reviewed');
  });

  it('viewing does not count as reviewed; changing or confirming does, and is saved', () => {
    render(<LabelingPage {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Next reply' }));
    expect(screen.getByText(/Reply 2 of 4/)).toBeTruthy();
    expect(progress()).toBe('0 of 4 reviewed');
    // Change r002's tone to fail.
    fireEvent.click(within(screen.getByRole('radiogroup', { name: /tone/ })).getByRole('radio', { name: 'Fail' }));
    expect(progress()).toBe('1 of 4 reviewed');
    expect(stored().r002.labels).toEqual(labels(true, false, true));
    expect(screen.getByText(/changed from the committed labels/)).toBeTruthy();
    // Confirm r003 as drafted.
    fireEvent.click(screen.getByRole('button', { name: 'Next reply' }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm/ }));
    expect(progress()).toBe('2 of 4 reviewed');
    expect(stored().r003).toEqual({ fp: props.cases[2].fp, labels: props.cases[2].labels });
    // Back to the committed labels forgets the review.
    fireEvent.click(screen.getByRole('button', { name: /Back to the committed labels/ }));
    expect(progress()).toBe('1 of 4 reviewed');
    expect(stored().r003).toBeUndefined();
  });

  it('starts from labels saved earlier in this browser', () => {
    window.localStorage.setItem(LABELS_KEY, JSON.stringify({ v: 1, replies: { r001: { fp: props.cases[0].fp, labels: labels(true, true, true) } } }));
    render(<LabelingPage {...props} />);
    expect(progress()).toBe('1 of 4 reviewed');
    const group = screen.getByRole('radiogroup', { name: /no_false_promises/ });
    expect((within(group).getByRole('radio', { name: 'Pass' }) as HTMLInputElement).checked).toBe(true);
  });

  it('Copy JSONL fills a read-only textarea in the reply_labels.jsonl shape and copies it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render(<LabelingPage {...props} />);
    fireEvent.click(within(screen.getByRole('radiogroup', { name: /no_false_promises/ })).getByRole('radio', { name: 'Pass' }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy JSONL' }));
    });
    const area = screen.getByLabelText(/reply_labels\.jsonl/) as HTMLTextAreaElement;
    expect(area.readOnly).toBe(true);
    const edited = props.cases.map((c) => c.labels);
    edited[0] = labels(true, true, true);
    expect(area.value).toBe(labelsJsonl(props.cases, edited));
    expect(writeText).toHaveBeenCalledWith(area.value);
    expect(screen.getByText(/Copied 4 lines/)).toBeTruthy();
  });

  it('when the clipboard fails, the textarea is the fallback', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }, configurable: true });
    render(<LabelingPage {...props} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy JSONL' }));
    });
    expect(screen.getByText(/select the text below and copy it/)).toBeTruthy();
    expect((screen.getByLabelText(/reply_labels\.jsonl/) as HTMLTextAreaElement).value).toBe(read('reply_labels.jsonl'));
  });

  it('works when browser storage is blocked, and says edits last only for this page', () => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get: () => {
        throw new Error('SecurityError');
      },
    });
    render(<LabelingPage {...props} />);
    fireEvent.click(within(screen.getByRole('radiogroup', { name: /tone/ })).getByRole('radio', { name: 'Fail' }));
    expect(progress()).toBe('1 of 4 reviewed');
    expect(screen.getByText(/storage is blocked/)).toBeTruthy();
  });
});

// ── Real data ───────────────────────────────────────────────────────────────
describe('real recorded judge data', () => {
  const real = loadLlmsData();
  const key = keyFor(real.prompts);

  it('judge cases: 30 labelled replies in both orders and 20 judged test replies per best prompt (R25)', () => {
    const p = judgeCardProps(real, key);
    const groups = (g: string) => p.cases.filter((c) => c.group === g);
    expect(groups('labelled')).toHaveLength(30);
    expect(groups('labelled').every((c) => Array.isArray(c.orders.after) && Array.isArray(c.orders.before))).toBe(true);
    expect(groups('v1_basic')).toHaveLength(20);
    expect(groups('v2_rules')).toHaveLength(20);
    const test = [...groups('v1_basic'), ...groups('v2_rules')];
    expect(test.every((c) => Array.isArray(c.orders.after) && !c.orders.before)).toBe(true);
    // Same ticket, two prompts: two different replies, two different verdict lines.
    const v1 = groups('v1_basic')[0];
    const v2 = groups('v2_rules').find((c) => c.caseId === v1.caseId)!;
    expect(v1.reply).not.toBe(v2.reply);
    // Everything: 70 cases, 49 tickets, 300 reasons (84,238 characters measured).
    expect(JSON.stringify(p).length).toBeLessThan(90_000);
    // JudgeCard.astro's default is DEFAULT_JUDGE_GROUPS: the labelled replies only, under 50 KB (R30).
    expect(DEFAULT_JUDGE_GROUPS).toEqual(['labelled']);
    const dflt = judgeCardProps(real, key, { groups: DEFAULT_JUDGE_GROUPS });
    expect(dflt.cases).toHaveLength(30);
    expect(JSON.stringify(dflt).length).toBeLessThan(50_000);
  });

  // Pinned from the Python lab (replay mode, no API calls), run from the repo root:
  //   python3 llms/python/triage_lab.py agreement
  //     before: addresses_issue 93.3% kappa 0.81, tone 93.3% 0.76, no_false_promises 93.3% 0.84
  //     after:  addresses_issue 93.3% kappa 0.81, tone 93.3% 0.76, no_false_promises 96.7% 0.92
  //     position flips: addresses_issue 0, tone 0, no_false_promises 1 of 30
  // and the exact values and flipped reply from llms/python:
  //   python3 -c "import triage_lab as T; p=T.load_prompts(); cs=T._label_cases(T.DATA_DIR, T.load_tickets());
  //     bo={o:[T.verdicts(T.judge(c['ticket'],c['reply'],order=o,prompts=p,case_id=c['id'])) for c in cs] for o in ('before','after')};
  //     print([(r, T.cohen_kappa([bool(v[r]) for v in bo['after']],[c['labels'][r] for c in cs])) for r in T.RUBRIC_IDS]);
  //     print([c['id'] for c,b,a in zip(cs,bo['before'],bo['after']) if b!=a])"
  //   -> after kappa 0.8148148148148149, 0.761904761904762, 0.9180327868852459; before nfp 0.8412698412698413; flipped ['r029']
  // REVIEWED 2026-10-28: these are the labels as reviewed by a human (r017 tone and r018 no_false_promises changed).
  it('agreement, kappa and flips match the Python lab on the reviewed labels', () => {
    const p = agreementProps(real, key);
    expect(p.recorded).toBe(true);
    expect(p.cases).toHaveLength(30);
    const human = p.cases.map((c) => c.file);
    const after = agreementStats(p.cases, human, 'after');
    const before = agreementStats(p.cases, human, 'before');
    const pick = (s: typeof after, f: 'agreement' | 'kappa' | 'flips') => s.map((x) => x[f]);
    expect(pick(after, 'agreement')).toEqual([28 / 30, 28 / 30, 29 / 30]);
    expect(pick(before, 'agreement')).toEqual([28 / 30, 28 / 30, 28 / 30]);
    expect(pick(after, 'kappa')[0]).toBeCloseTo(0.8148148148148149, 12);
    expect(pick(after, 'kappa')[1]).toBeCloseTo(0.761904761904762, 12);
    expect(pick(after, 'kappa')[2]).toBeCloseTo(0.9180327868852459, 12);
    expect(pick(before, 'kappa')[0]).toBeCloseTo(0.8148148148148149, 12);
    expect(pick(before, 'kappa')[1]).toBeCloseTo(0.761904761904762, 12);
    expect(pick(before, 'kappa')[2]).toBeCloseTo(0.8412698412698413, 12);
    expect(pick(after, 'flips')).toEqual([0, 0, 1]);
    expect(p.cases.filter((c) => c.before.some((v, i) => v !== c.after[i])).map((c) => c.id)).toEqual(['r029']);
    expect(after.map((s) => s.disagree)).toEqual([['r003', 'r022'], ['r012', 'r017'], ['r018']]);
    expect(before.map((s) => s.disagree)).toEqual([['r003', 'r022'], ['r012', 'r017'], ['r018', 'r029']]);
  });

  it('the props stay small (R30)', () => {
    expect(JSON.stringify(agreementProps(real, key)).length).toBeLessThan(5_000);
    expect(JSON.stringify(labelingProps(real)).length).toBeLessThan(30_000);
  });
});
