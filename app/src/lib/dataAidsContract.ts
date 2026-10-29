// ContractViewer (lessons 4-5): a contract rendered section by section from its export (contract.py --export
// writes <id>.contract.json), and, in lesson 5, beside each section the verdicts its rules produced on the files
// the contract governs (from verdicts.json). Pure and browser-safe; dataData.ts reads the files at build time.
// Re-exported by dataAids.ts.
import {
  SECTIONS,
  entryView,
  plural,
  ruleSection,
  type ContractJson,
  type FieldJson,
  type QualityEntryView,
  type Section,
  type VerdictJson,
  type VerdictsJson,
} from './dataAidsTypes';

export type ContractViewerProps = {
  /** The export, unchanged. */
  contract: ContractJson;
  /** Each verdicts.json entry checked against this contract; [] unless the lesson asks for verdicts. */
  checks: QualityEntryView[];
};

export function contractViewerProps(
  contracts: Record<string, ContractJson>,
  verdicts: VerdictsJson,
  opts: { contract: string; verdicts?: boolean },
): ContractViewerProps {
  const contract = contracts[opts.contract];
  if (!contract) throw new Error(`ContractViewer: no contract "${opts.contract}" (known: ${Object.keys(contracts).sort().join(', ')})`);
  const checks = opts.verdicts
    ? Object.entries(verdicts.datasets)
        .filter(([, e]) => e.contract === opts.contract)
        .map(([k, e]) => entryView(k, e))
    : [];
  return { contract, checks };
}

/** "enum: billing, bug, ..." or "pattern ^t\d{3}$"; a dash for a field with neither. */
export function fieldRule(f: FieldJson): string {
  const parts: string[] = [];
  if (f.enum) parts.push(`enum: ${f.enum.join(', ')}`);
  if (f.pattern) parts.push(`pattern ${f.pattern}`);
  return parts.length ? parts.join('; ') : '—';
}

/** The quality section as [rule, value] rows, in the export's order. */
export function qualityLines(q: ContractJson['quality']): [string, string][] {
  return Object.entries(q).map(([rule, v]) => {
    if (Array.isArray(v)) return [rule, v.join(', ')];
    const r = v as { min?: number; max?: number };
    return [rule, [r.min !== undefined ? `min ${r.min}` : '', r.max !== undefined ? `max ${r.max}` : ''].filter(Boolean).join(', ')];
  });
}

/** The info section as [key, value] rows, in the export's order. */
export const infoLines = (c: ContractJson): [string, string][] => Object.entries(c.info).map(([k, v]) => [k, String(v)]);

/** What one section says, for the live region: "Section 2 of 5: schema. 6 fields, 6 required." */
export function sectionSummary(c: ContractJson, i: number): string {
  const name = SECTIONS[i];
  const head = `Section ${i + 1} of ${SECTIONS.length}: ${name}.`;
  switch (name) {
    case 'info': {
      const sunset = c.info.sunset ? `, sunset ${c.info.sunset}` : '';
      return `${head} ${c.info.id}, version ${c.info.version}, ${c.info.status}${sunset}.`;
    }
    case 'schema': {
      const required = c.schema.fields.filter((f) => f.required).length;
      return `${head} ${plural(c.schema.fields.length, 'field')}, ${required} required.`;
    }
    case 'quality': {
      const rules = Object.keys(c.quality);
      return `${head} ${plural(rules.length, 'rule')}: ${rules.join(', ')}.`;
    }
    case 'servicing': {
      const f = c.servicing.freshness;
      return `${head} ${f ? `Freshness: at most ${plural(f.max_age_days, 'day')} old.` : 'No freshness promise.'}`;
    }
    default: {
      const who = c.consumers.map((x) => `${x.name} on version ${x.version}`).join(', ');
      return `${head} ${plural(c.consumers.length, 'consumer')}: ${who}.`;
    }
  }
}

/** The verdicts whose rule this section declares (info and consumers declare none). */
export const verdictsFrom = (e: QualityEntryView, section: Section): VerdictJson[] => e.verdicts.filter((v) => ruleSection(v.rule) === section);

/** The live line with verdicts beside: the section, then how many of the entry's verdicts it produced. */
export function sectionLine(c: ContractJson, i: number, e: QualityEntryView | undefined): string {
  const head = sectionSummary(c, i);
  if (!e) return head;
  const n = verdictsFrom(e, SECTIONS[i]).length;
  if (n === 0) return `${head} No verdict comes from this section.`;
  return `${head} ${n} of the ${e.verdicts.length} verdicts on ${e.key} ${n === 1 ? 'comes' : 'come'} from this section.`;
}
