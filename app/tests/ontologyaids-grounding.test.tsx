// @vitest-environment jsdom
// The Ontology track's grounding aids: GroundingReport (lesson 3) and AuditTable (lesson 4), plus the track's
// props budget. Every number an aid shows is pinned here as a literal taken from ontology/data/derived/
// generated.json (generate.py), validation.json (grounding.py) and audit.json (audit.py); the gate's verdict is
// pinned verbatim.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
// Astro's own prop serialiser and escaper: the budget measures the props as the built page carries them.
import { serializeProps } from 'astro/runtime/server/serialize.js';
import { escapeHTML } from 'astro/runtime/server/escape.js';
import { loadOntologyData } from '../src/lib/ontologyData';
import {
  auditCounts,
  auditLine,
  auditTableProps,
  diffLines,
  groundingReportProps,
  manualNote,
  ontologyBrowserProps,
  sourceStatus,
  statusMark,
  validationLine,
  type GeneratedReportProps,
  type ValidationReportProps,
} from '../src/lib/ontologyAids';
import GroundingReport from '../src/components/ontology/GroundingReport';
import AuditTable from '../src/components/ontology/AuditTable';

const data = loadOntologyData();
const live = () => document.querySelector('[aria-live="polite"]')?.textContent;
const select = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;
const generated = () => groundingReportProps(data, { show: 'generated' }) as GeneratedReportProps;
const validation = () => groundingReportProps(data, { show: 'validation' }) as ValidationReportProps;

afterEach(() => cleanup());

// ── GroundingReport: generated ──────────────────────────────────────────────
describe('grounding report data: generated', () => {
  it('carries the three artefacts, each named by where the existing one lives', () => {
    const p = generated();
    expect(p.show).toBe('generated');
    expect(p.artefacts.map((a) => [a.id, a.existingFrom, a.generatedFrom, a.added, a.removed])).toEqual([
      ['definitions', 'prompts.json triage.v2_rules', 'generated definitions', 10, 14],
      ['schema', 'triage_lab.py TRIAGE_SCHEMA', 'generated json_schema', 7, 7],
      ['tool', 'ops_server.py label_ticket', 'generated tool_definition', 9, 9],
    ]);
    expect(p.artefacts[0].note).toBe("The v3_examples prompt's rule lines are the same as v2_rules', so this one comparison covers both.");
    expect(p.artefacts[1].note).toBeNull();
    expect(p.artefacts[1].existing.split('\n')[0]).toBe('{');
    expect(p.artefacts[1].existing).toContain('  "additionalProperties": false,');
  });

  it('marks each diff line by a sign and a word, not colour alone', () => {
    const lines = diffLines(generated().artefacts[1].diff);
    expect(lines.slice(0, 3).map((l) => l.kind)).toEqual(['header', 'header', 'hunk']);
    expect(lines.filter((l) => l.kind === 'added')).toHaveLength(7);
    expect(lines.filter((l) => l.kind === 'removed')).toHaveLength(7);
    expect(lines.find((l) => l.kind === 'added')!.text).toBe('+        "outage",');
    expect(lines.map((l) => l.text).join('\n')).toBe(generated().artefacts[1].diff.replace(/\n$/, ''));
  });

  it('refuses a show it does not know', () => {
    expect(() => groundingReportProps(data, { show: 'nope' as 'generated' })).toThrow(/GroundingReport: show must be "generated" or "validation", not "nope"/);
  });
});

describe('GroundingReport (generated)', () => {
  it('shows the existing and generated artefact side by side, and the diff in its own scroll box', () => {
    render(<GroundingReport {...generated()} />);
    expect(live()).toBe('definitions: prompts.json triage.v2_rules against generated definitions: 10 lines added, 14 removed.');
    expect(screen.getByRole('region', { name: 'Existing: prompts.json triage.v2_rules' }).textContent).toContain('Categories (take the first that matches):');
    expect(screen.getByRole('region', { name: 'Generated: generated definitions' }).textContent).toMatch(/^- outage: The service/);
    const diff = screen.getByRole('region', { name: 'Diff' });
    expect(diff.getAttribute('tabindex')).toBe('0');
    expect(diff.textContent).toContain('--- prompts.json triage.v2_rules');
    expect(screen.getByText(/so this one comparison covers both/)).toBeTruthy();
  });

  it('switches artefact from the picker and announces it', () => {
    render(<GroundingReport {...generated()} />);
    fireEvent.change(select('Artefact'), { target: { value: 'tool' } });
    expect(live()).toBe('tool: ops_server.py label_ticket against generated tool_definition: 9 lines added, 9 removed.');
    expect(screen.getByRole('region', { name: 'Diff' }).textContent).toContain('-  "error_label": "label must be one of: billing, bug, account, how_to, feature_request, outage",');
    expect(screen.getByRole('region', { name: 'Diff' }).querySelectorAll('.on-diff-add')).toHaveLength(9);
  });
});

// ── GroundingReport: validation ─────────────────────────────────────────────
describe('grounding report data: validation', () => {
  it('counts per source as validation.json has them, failing sources marked', () => {
    const p = validation();
    expect(p.records).toBe(700);
    expect(p.violations).toBe(7);
    expect(p.manualConstraints).toBe(24);
    expect(p.checkable).toBe(1);
    expect(p.sources).toHaveLength(16);
    const row = (name: string) => p.sources.find((s) => s.name === name)!;
    expect(row('triage__claude-haiku-5-5__v1_basic__default')).toEqual({
      name: 'triage__claude-haiku-5-5__v1_basic__default', records: 60, violations: [['feature_request_never_above_normal', 2]], violating: ['t029', 't055'], manual: 24, unchecked: [], ok: false,
    });
    expect(row('agents/desk-hand')).toEqual({ name: 'agents/desk-hand', records: 17, violations: [], violating: [], manual: 25, unchecked: ['feature_request_never_above_normal'], ok: true });
    expect(row('Qwen2.5-0.5B-Instruct+lora__lora__default')).toMatchObject({ records: 20, violating: ['t055'], ok: false });
    expect(p.sources.filter((s) => !s.ok).map((s) => s.name)).toEqual([
      'Qwen2.5-0.5B-Instruct+lora__lora__default',
      'triage__claude-haiku-5-5__v1_basic__default',
      'triage__claude-opus-5-5__v1_basic__high',
      'triage__claude-opus-5-5__v1_basic__low',
      'triage__claude-sonnet-5-5__v1_basic__high',
      'triage__claude-sonnet-5-5__v1_basic__low',
    ]);
    expect(sourceStatus(row('agents/desk-hand'))).toBe('PASS');
    expect(sourceStatus(row('triage__claude-opus-5-5__v1_basic__high'))).toBe('FAIL');
  });

  it('counts per violation type, every checkable constraint listed', () => {
    expect(validation().types).toEqual([
      {
        rule: 'feature_request_never_above_normal',
        text: 'A feature_request is never above normal, because support can\'t fix a missing feature.',
        count: 7,
        sources: 6,
      },
    ]);
  });

  it('says why a few sources count one more manual constraint', () => {
    expect(manualNote(validation())).toBe(
      "24 manual constraints are left to a person: no program can check them. Manual counts the constraints each source's records still need judged; 4 sources count 25, because their records lack a property feature_request_never_above_normal reads, so it could not run either.",
    );
  });

  it('says the totals in one line', () => {
    expect(validationLine(validation(), false)).toBe(
      '700 records from 16 sources: 7 violations of 1 checkable constraint, in 6 sources. Showing all 16 sources.',
    );
    expect(validationLine(validation(), true)).toBe(
      '700 records from 16 sources: 7 violations of 1 checkable constraint, in 6 sources. Showing the 6 that fail.',
    );
  });

  it('fails loudly when the sources do not add up to the totals', () => {
    const json = structuredClone(data);
    json.validation.totals.records = 701;
    expect(() => groundingReportProps(json, { show: 'validation' })).toThrow(/validation.json: the sources hold 700 records, but totals says 701/);
    const v = structuredClone(data);
    v.validation.totals.violations.feature_request_never_above_normal = 8;
    expect(() => groundingReportProps(v, { show: 'validation' })).toThrow(/validation.json: the sources hold 7 feature_request_never_above_normal violations, but totals says 8/);
  });
});

describe('GroundingReport (validation)', () => {
  it('lists every source with PASS and FAIL in words, and the manual count', () => {
    render(<GroundingReport {...validation()} />);
    expect(live()).toBe('700 records from 16 sources: 7 violations of 1 checkable constraint, in 6 sources. Showing all 16 sources.');
    const rows = within(screen.getByRole('table', { name: 'Validation per source' })).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(16);
    expect(rows[0].textContent).toBe('Qwen2.5-0.5B-Instruct+lora__lora__default201t05524✗ FAIL');
    expect(rows[1].textContent).toBe('agents/desk-hand170—25✓ PASS');
    const types = within(screen.getByRole('table', { name: 'Violations per type' })).getAllByRole('row').slice(1);
    expect(types.map((r) => r.textContent)).toEqual(['feature_request_never_above_normal76']);
    expect(screen.getByText(/24 manual constraints/)).toBeTruthy();
  });

  it('shows the failing sources only on request, and announces it', () => {
    render(<GroundingReport {...validation()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show failing sources only' }));
    expect(screen.getByRole('button', { name: 'Show failing sources only' }).getAttribute('aria-pressed')).toBe('true');
    expect(within(screen.getByRole('table', { name: 'Validation per source' })).getAllByRole('row').slice(1)).toHaveLength(6);
    expect(live()).toMatch(/Showing the 6 that fail\.$/);
  });
});

// ── AuditTable ──────────────────────────────────────────────────────────────
describe('audit table data', () => {
  it('takes every row by default, the artefacts in audit.json order, and the gate as written', () => {
    const p = auditTableProps(data.audit);
    expect(p.rows).toHaveLength(82);
    expect(p.artefacts).toEqual([
      'data/contracts/complaints.v1.yaml',
      'data/contracts/tickets.v1.yaml',
      'data/contracts/tickets.v2.yaml',
      'llms/data/labeling_guide.md',
      'llms/data/prompts.json#triage.v2_rules',
      'llms/data/prompts.json#triage.v3_examples',
      'ml/data/category_map.json',
    ]);
    expect(p.narrowed).toBe(false);
    expect(p.gate.reason).toBe('red: 1 disagree (data/contracts/tickets.v2.yaml enum:category); 0 missing');
    expect(auditCounts(p.rows)).toEqual({ agree: 81, disagree: 1, missing: 0 });
  });

  it('narrows to the artefacts a lesson names, and refuses one the audit does not have', () => {
    const p = auditTableProps(data.audit, { artefacts: ['data/contracts/tickets.v1.yaml', 'data/contracts/tickets.v2.yaml'] });
    expect(p.rows.map((r) => `${r.fact} ${r.status}`)).toEqual(['enum:category agree', 'enum:priority agree', 'enum:category disagree', 'enum:urgency agree']);
    expect(p.narrowed).toBe(true);
    expect(() => auditTableProps(data.audit, { artefacts: ['nope.yaml'] })).toThrow(/AuditTable: unknown artefact "nope.yaml" \(known: data\/contracts\/complaints.v1.yaml/);
  });

  it('marks agree, disagree and missing by a symbol and a word', () => {
    expect(statusMark('agree')).toEqual({ icon: '✓', word: 'AGREE', tone: 'ok' });
    expect(statusMark('disagree')).toEqual({ icon: '✗', word: 'DISAGREE', tone: 'fail' });
    expect(statusMark('missing')).toEqual({ icon: '?', word: 'MISSING', tone: 'warn' });
  });

  it('says what the table shows', () => {
    const p = auditTableProps(data.audit);
    expect(auditLine(p, false)).toBe('82 facts from 7 artefacts: 81 agree, 1 disagree, 0 missing. Showing all 82.');
    expect(auditLine(p, true)).toBe('82 facts from 7 artefacts: 81 agree, 1 disagree, 0 missing. Showing the 1 that disagree or are missing.');
  });
});

describe('AuditTable', () => {
  it('shows one row per artefact per fact, and the gate verdict verbatim', () => {
    render(<AuditTable {...auditTableProps(data.audit)} />);
    expect(live()).toBe('82 facts from 7 artefacts: 81 agree, 1 disagree, 0 missing. Showing all 82.');
    const rows = within(screen.getByRole('table', { name: 'Audit rows' })).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(82);
    expect(rows[0].textContent).toBe('data/contracts/complaints.v1.yamlenum:product✓ AGREEthe 6 products');
    expect(rows[3].textContent).toBe('data/contracts/tickets.v2.yamlenum:category✗ DISAGREEextra value the ontology lacks: security');
    const status = screen.getByRole('status');
    expect(status.textContent).toContain('red: 1 disagree (data/contracts/tickets.v2.yaml enum:category); 0 missing');
    expect(status.textContent).toContain('✗ RED');
    const per = within(screen.getByRole('table', { name: 'Audit per artefact' })).getAllByRole('row').slice(1);
    expect(per.map((r) => r.textContent)).toEqual([
      'data/contracts/complaints.v1.yaml100',
      'data/contracts/tickets.v1.yaml200',
      'data/contracts/tickets.v2.yaml110',
      'llms/data/labeling_guide.md4900',
      'llms/data/prompts.json#triage.v2_rules1100',
      'llms/data/prompts.json#triage.v3_examples1100',
      'ml/data/category_map.json600',
    ]);
  });

  it('filters to the disagreements on request, and announces it', () => {
    render(<AuditTable {...auditTableProps(data.audit)} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show disagreements only' }));
    const rows = within(screen.getByRole('table', { name: 'Audit rows' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual(['data/contracts/tickets.v2.yamlenum:category✗ DISAGREEextra value the ontology lacks: security']);
    expect(live()).toMatch(/Showing the 1 that disagree or are missing\.$/);
  });

  it('says the gate covers the whole audit when the table is narrowed', () => {
    render(<AuditTable {...auditTableProps(data.audit, { artefacts: ['ml/data/category_map.json'] })} />);
    expect(live()).toBe('6 facts from 1 artefact: 6 agree, 0 disagree, 0 missing. Showing all 6.');
    expect(screen.getByRole('status').textContent).toContain('The gate counts every artefact, not only the 1 shown.');
  });
});

// ── Page budget (spec §6: the track's props under 200 KB) ───────────────────
// What the lessons plan to ship (spec §5: OntologyBrowser in lesson 2, GroundingReport in 3, AuditTable in 4),
// taken at its largest: every class, both GroundingReport views, every audit row; serialised and escaped by
// Astro's own code as the built page carries them. The built page itself is measured beside the agents budget in
// agentsaids-evals.test.tsx, over the four ontology lessons.
const shipped = (props: object) => Buffer.byteLength(escapeHTML(serializeProps(props, {} as never)), 'utf8');

describe('page budget', () => {
  it('keeps the largest props the ontology lessons could ship under 200 KB', () => {
    const parts: [string, number][] = [
      ['OntologyBrowser', shipped(ontologyBrowserProps(data.ontology))],
      ['GroundingReport generated', shipped(groundingReportProps(data, { show: 'generated' }))],
      ['GroundingReport validation', shipped(groundingReportProps(data, { show: 'validation' }))],
      ['AuditTable', shipped(auditTableProps(data.audit))],
    ];
    const total = parts.reduce((a, [, n]) => a + n, 0);
    console.info(`ontology props: ${parts.map(([k, n]) => `${k} ${n}`).join(', ')}; total ${total}`);
    expect(total, parts.map(([k, n]) => `${k} ${n}`).join(', ')).toBeLessThan(200_000);
    for (const [k, n] of parts) expect(n, k).toBeGreaterThan(500);
  });
});
