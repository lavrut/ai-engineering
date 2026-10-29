// OntologyBrowser (lesson 2): pick a class of Lumen's ontology and see its definition, its properties with range
// and cardinality, the relations out of it and into it, its individuals in the guide's decision order, and the
// constraints declared on it with their kind. Pure and browser-safe; ontologyData.ts reads ontology.json at build
// time. Re-exported by ontologyAids.ts.
import { plural, type ConditionJson, type OntologyJson, type PropertyJson, type RelationJson } from './ontologyAidsTypes';

/** A property's range, said three ways: a literal type, a closed list of a class's individuals, or of values. */
export type RangeView = { kind: 'literal'; type: string } | { kind: 'individuals'; of: string | null; ids: string[] } | { kind: 'values'; values: string[] };
export type PropertyView = { id: string; range: RangeView; cardinality: 'one' | 'many'; required: boolean | null; pattern: string | null };
export type IndividualView = { id: string; order: number; definition: string; source: string; label: string | null };
export type ConstraintView = {
  id: string;
  kind: 'checkable' | 'manual';
  source: string;
  text: string;
  when: ConditionJson[] | null;
  then: ConditionJson[] | null;
  phrases: string[] | null;
};
export type ClassView = {
  id: string;
  label: string;
  definition: string;
  properties: PropertyView[];
  /** Relations whose domain is this class, and those whose range is (a relation is in two classes' views). */
  out: RelationJson[];
  in: RelationJson[];
  individuals: IndividualView[];
  constraints: ConstraintView[];
};
export type OntologyBrowserProps = { source: string; classes: ClassView[]; initial: string };

function rangeView(p: PropertyJson, o: OntologyJson): RangeView {
  const r = p.range;
  if (typeof r === 'string') return { kind: 'literal', type: r };
  if ('individuals' in r) {
    const of = o.individuals.find((i) => i.id === r.individuals[0])?.class ?? null;
    return { kind: 'individuals', of, ids: r.individuals };
  }
  return { kind: 'values', values: r.values };
}

function classView(id: string, o: OntologyJson): ClassView {
  const c = o.classes.find((x) => x.id === id)!;
  return {
    id,
    label: c.label,
    definition: c.definition,
    properties: o.properties
      .filter((p) => p.domain === id)
      .map((p) => ({ id: p.id, range: rangeView(p, o), cardinality: p.cardinality, required: p.required ?? null, pattern: p.pattern ?? null })),
    out: o.relations.filter((r) => r.domain === id),
    in: o.relations.filter((r) => r.range === id),
    individuals: o.individuals
      .filter((i) => i.class === id)
      .map((i) => ({ id: i.id, order: i.order, definition: i.definition, source: i.source, label: i.label ?? null }))
      .sort((a, b) => a.order - b.order),
    constraints: o.constraints
      .filter((x) => x.class === id)
      .map((x) => ({ id: x.id, kind: x.kind, source: x.source, text: x.text, when: x.when ?? null, then: x.then ?? null, phrases: x.phrases ?? null })),
  };
}

/** Every class in ontology.json's order; `cls` picks the one shown first (the first class by default). */
export function ontologyBrowserProps(o: OntologyJson, opts: { cls?: string } = {}): OntologyBrowserProps {
  const ids = o.classes.map((c) => c.id);
  const initial = opts.cls ?? ids[0];
  if (!ids.includes(initial)) throw new Error(`OntologyBrowser: unknown class "${initial}" (known: ${ids.join(', ')})`);
  return { source: o.source, classes: ids.map((id) => classView(id, o)), initial };
}

// ── Words ───────────────────────────────────────────────────────────────────
/** "string", "one of Category: outage, billing, ...", "one of the values: dev, test". */
export function rangeText(r: RangeView): string {
  if (r.kind === 'literal') return r.type;
  if (r.kind === 'individuals') return `one of ${r.of ?? 'the individuals'}: ${r.ids.join(', ')}`;
  return `one of the values: ${r.values.join(', ')}`;
}

/** "exactly one, required", "many, required", "exactly one". */
export function cardinalityText(c: 'one' | 'many', required: boolean | null): string {
  const n = c === 'one' ? 'exactly one' : 'many';
  return required ? `${n}, required` : n;
}

const OPS: Record<string, string> = { eq: '=', ne: '≠', in: 'in', not_in: 'not in' };
const valueText = (v: unknown) => (Array.isArray(v) ? v.join(', ') : String(v));

/** "category = feature_request", "priority in normal, low". */
export const conditionText = (c: ConditionJson) => `${c.property} ${OPS[c.op] ?? c.op} ${valueText(c.value)}`;

/** A checkable constraint as its rule ("when ..., then ..."); null for a manual one, which a person judges. */
export function constraintRule(c: ConstraintView): string | null {
  if (c.kind !== 'checkable' || !c.then) return null;
  const then = c.then.map(conditionText).join(' and ');
  return c.when?.length ? `when ${c.when.map(conditionText).join(' and ')}, then ${then}` : `always ${then}`;
}

/** "Ticket: 6 properties, 3 relations out, 1 relation in, 0 individuals, 25 constraints (1 checkable, 24 manual)." */
export function classSummary(c: ClassView): string {
  const checkable = c.constraints.filter((x) => x.kind === 'checkable').length;
  const kinds = c.constraints.length ? ` (${checkable} checkable, ${c.constraints.length - checkable} manual)` : '';
  return (
    `${c.id}: ${c.properties.length} ${c.properties.length === 1 ? 'property' : 'properties'}, ${plural(c.out.length, 'relation')} out, ` +
    `${plural(c.in.length, 'relation')} in, ${plural(c.individuals.length, 'individual')}, ${plural(c.constraints.length, 'constraint')}${kinds}.`
  );
}

/** The class a key moves to in the class list: arrows step (and wrap), Home and End jump; null for any other key. */
export function neighbour(ids: string[], current: string, key: string): string | null {
  const i = ids.indexOf(current);
  if (key === 'Home') return ids[0];
  if (key === 'End') return ids[ids.length - 1];
  if (key === 'ArrowRight' || key === 'ArrowDown') return ids[(i + 1) % ids.length];
  if (key === 'ArrowLeft' || key === 'ArrowUp') return ids[(i - 1 + ids.length) % ids.length];
  return null;
}
