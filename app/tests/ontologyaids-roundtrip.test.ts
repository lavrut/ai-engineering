// Round trips for the ontology aids' props: the aids reshape the derived JSON into what a lesson shows, but never
// rewrite it. Every record they ship is taken back to the record it came from and compared field by field, so a
// reshaping that drops, renames or rounds a value fails here.
// (a) OntologyBrowser: the class views rebuild ontology.json exactly: each class, each property (its range back
//     from the literal / individuals / values encoding), each relation (once out of its domain, once into its
//     range), each individual and each constraint, in the file's order.
// (b) GroundingReport: each artefact rebuilds its generated.json compare entry (a JSON artefact back from its
//     pretty-printed text); each source rebuilds its validation.json entry.
// (c) AuditTable: the rows are audit.json's rows, in order, and the gate is audit.json's gate.
// (d) ActionMatrix: the cells rebuild governance.json's matrix, cell by cell and engine by engine; the tools,
//     conditions, totals and gate are governance.json's.
import { describe, expect, it } from 'vitest';
import { loadOntologyData } from '../src/lib/ontologyData';
import {
  ALL_ENGINES,
  actionMatrixProps,
  auditTableProps,
  groundingReportProps,
  ontologyBrowserProps,
  type ConstraintJson,
  type GeneratedReportProps,
  type IndividualJson,
  type MatrixCellJson,
  type OntologyJson,
  type PropertyJson,
  type RangeJson,
  type RangeView,
  type ValidationReportProps,
} from '../src/lib/ontologyAids';

const data = loadOntologyData();
const byOrder = <T extends { id: string }>(xs: T[], order: string[]) => [...xs].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));

function rangeBack(r: RangeView): RangeJson {
  if (r.kind === 'literal') return r.type;
  if (r.kind === 'individuals') return { individuals: r.ids };
  return { values: r.values };
}

describe('OntologyBrowser round trip', () => {
  const p = ontologyBrowserProps(data.ontology);
  const o = data.ontology;

  it('rebuilds ontology.json from the class views', () => {
    const back: Omit<OntologyJson, 'actions'> = { // section 6 is not the browser's: ActionMatrix shows it
      source: p.source,
      classes: p.classes.map((c) => ({ definition: c.definition, id: c.id, label: c.label })),
      properties: p.classes.flatMap((c) =>
        c.properties.map((x): PropertyJson => ({
          cardinality: x.cardinality,
          domain: c.id,
          id: x.id,
          ...(x.pattern === null ? {} : { pattern: x.pattern }),
          range: rangeBack(x.range),
          ...(x.required === null ? {} : { required: x.required }),
        })),
      ),
      relations: p.classes.flatMap((c) => c.out),
      individuals: p.classes.flatMap((c) =>
        c.individuals.map((i): IndividualJson => ({
          class: c.id,
          definition: i.definition,
          id: i.id,
          ...(i.label === null ? {} : { label: i.label }),
          order: i.order,
          source: i.source,
        })),
      ),
      constraints: p.classes.flatMap((c) =>
        c.constraints.map((x): ConstraintJson => ({
          class: c.id,
          id: x.id,
          kind: x.kind,
          ...(x.phrases === null ? {} : { phrases: x.phrases }),
          source: x.source,
          text: x.text,
          ...(x.then === null ? {} : { then: x.then }),
          ...(x.when === null ? {} : { when: x.when }),
        })),
      ),
    };
    expect(back.classes).toEqual(o.classes);
    expect(byOrder(back.properties, o.properties.map((x) => x.id))).toEqual(o.properties);
    expect(byOrder(back.relations, o.relations.map((x) => x.id))).toEqual(o.relations);
    expect(byOrder(back.individuals, o.individuals.map((x) => x.id))).toEqual(o.individuals);
    expect(byOrder(back.constraints, o.constraints.map((x) => x.id))).toEqual(o.constraints);
    expect(back.source).toBe(o.source);
  });

  it('carries each relation once out of its domain and once into its range', () => {
    const ins = p.classes.flatMap((c) => c.in.map((r) => [c.id, r] as const));
    expect(ins).toHaveLength(o.relations.length);
    for (const [cls, r] of ins) expect(r.range).toBe(cls);
    expect(byOrder(ins.map(([, r]) => r), o.relations.map((x) => x.id))).toEqual(o.relations);
  });

  it('names the class whose individuals an individuals range lists', () => {
    for (const c of p.classes) {
      for (const x of c.properties) {
        if (x.range.kind !== 'individuals') continue;
        const of = x.range.of;
        for (const id of x.range.ids) expect(o.individuals.find((i) => i.id === id)?.class, `${x.id} ${id}`).toBe(of);
      }
    }
  });
});

describe('GroundingReport round trip', () => {
  it('rebuilds each generated.json compare entry', () => {
    const p = groundingReportProps(data, { show: 'generated' }) as GeneratedReportProps;
    expect(p.artefacts.map((a) => a.id)).toEqual(Object.keys(data.generated.compare));
    for (const a of p.artefacts) {
      const json = data.generated.compare[a.id];
      const back = (text: string, kind: 'text' | 'json') => (kind === 'json' ? JSON.parse(text) : text);
      expect(back(a.existing, a.existingKind), a.id).toEqual(json.existing);
      expect(back(a.generated, a.generatedKind), a.id).toEqual(json.generated);
      expect(a.diff, a.id).toBe(json.diff);
      expect(a.note === null, a.id).toBe(json.v3_rule_lines_equal_v2 !== true);
    }
  });

  it('rebuilds each validation.json source', () => {
    const p = groundingReportProps(data, { show: 'validation' }) as ValidationReportProps;
    expect(p.sources.map((s) => s.name)).toEqual(Object.keys(data.validation.sources));
    for (const s of p.sources) {
      const json = data.validation.sources[s.name];
      expect({ records: s.records, violations: Object.fromEntries(s.violations), violating: s.violating }, s.name).toEqual({
        records: json.records, violations: json.violations, violating: json.violating,
      });
      expect(s.manual, s.name).toBe(Object.keys(json.manual).length);
      expect(s.ok, s.name).toBe(Object.keys(json.violations).length === 0);
    }
  });
});

describe('AuditTable round trip', () => {
  it("ships audit.json's rows and gate unchanged", () => {
    const p = auditTableProps(data.audit);
    expect(p.rows).toEqual(data.audit.rows);
    expect(p.gate).toEqual(data.audit.gate);
  });
});

describe('ActionMatrix round trip', () => {
  it("rebuilds governance.json's matrix from the cells", () => {
    const g = data.governance;
    const p = actionMatrixProps(g);
    const back = p.cells.map((c): MatrixCellJson => {
      const { events, ...verdicts } = c.counts[ALL_ENGINES];
      return {
        action: c.action,
        by_engine: Object.fromEntries(p.engines.map((e) => [e, c.counts[e]])),
        conditions: c.conditions,
        events,
        ...(c.landed === null ? {} : { landed: c.landed }),
        role: c.role,
        rule: c.rule,
        unmet: c.unmet,
        verdicts,
      };
    });
    expect(back).toEqual(g.matrix);
    expect(Object.fromEntries(p.tools)).toEqual(g.tools);
    expect(p.conditions).toEqual(g.conditions.map(({ id, text }) => ({ id, text })));
    expect(p.gate).toEqual(g.gate);
    expect(p.deskRuns).toBe(g.totals.desk_runs);
    for (const e of p.engines) {
      const desks = Object.values(g.by_engine[e]);
      expect(p.totals[e].events, e).toBe(desks.reduce((a, d) => a + d.events, 0));
    }
  });
});
