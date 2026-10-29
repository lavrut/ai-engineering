import type { Diagram } from '../../lib/diagram';

// Three ways to give an agent meaning, and how high each reaches. On the left, the pyramid, drawn as three stacked
// levels from the top: permitted actions, definitions, data (the label on the edge between two levels, drawn to its
// left, says what the upper one adds). In the middle, the three architectures, each on the level it reaches. On the
// right, the course artefact each one stands on, joined to it. diagrams.test.ts pins every number a caption states
// against governance.json. Each linked caption ends with its section.
const LEVEL_X = 250;
const ARCH_X = 560;
const ARCH_W = 190;
const ART_X = 790;
const ART_W = 190;
const level = (id: string, label: string, y: number, grow: number) => ({ id, label, role: 'memory' as const, x: LEVEL_X - grow, y, w: 200 + 2 * grow });
const arch = (id: string, label: string, y: number) => ({ id, label, role: 'harness' as const, x: ARCH_X, y, w: ARCH_W });
const artefact = (id: string, label: string, y: number) => ({ id, label, role: 'dataset' as const, x: ART_X, y, w: ART_W });
const edge = (from: string, to: string, label?: string) => ({ id: `e-${from}-${to}`, from, to, ...(label ? { label } : {}) });

export const ontologyArchitectures: Diagram = {
  id: 'ontology-architectures',
  title: "Inferred context, definitions, governed action: how high each reaches, and the course's artefact for each",
  width: 1000,
  height: 440,
  nodes: [
    level('permitted-actions', 'Permitted actions', 30, 0),
    level('definitions', 'Definitions', 190, 30),
    level('data', 'Data', 350, 60),
    arch('governed', 'Governed action', 30),
    arch('explicit', 'Written definitions', 190),
    arch('inferred', 'Inferred context', 350),
    artefact('actions-section', 'lumen.yaml actions', 30),
    artefact('lumen-ontology', 'ontology/lumen.yaml', 190),
    artefact('data-contracts', 'data/contracts/*.yaml', 350),
  ],
  edges: [
    edge('data', 'definitions', '+ meaning, written once'),
    edge('definitions', 'permitted-actions', '+ who may do what'),
    edge('inferred', 'data'),
    edge('explicit', 'definitions'),
    edge('governed', 'permitted-actions'),
    edge('data-contracts', 'inferred'),
    edge('lumen-ontology', 'explicit'),
    edge('actions-section', 'governed'),
  ],
  steps: [
    {
      n: 1,
      highlight: ['data', 'e-inferred-data', 'inferred', 'e-data-contracts-inferred', 'data-contracts'],
      caption:
        "Inferred context: the agent reads the data and what describes it, a schema, column names, a catalogue entry, and works out at run time what the values mean. The data track's contracts are that kind of metadata: they say category is a string from a closed list and what the field is for, not when a ticket is an outage. The meaning lives in the model's guess. In the code: section 2.",
      code: { file: 'python', section: 2, source: 'data-contract-tickets' },
    },
    {
      n: 2,
      highlight: ['data', 'e-data-definitions', 'definitions', 'e-explicit-definitions', 'explicit', 'e-lumen-ontology-explicit', 'lumen-ontology'],
      caption:
        "Written definitions: the meaning is written down once, in the ontology, and the prompt's definitions, the schema and the tool are generated from it. A person can read every definition and audit every artefact against it. In the code: section 4.",
      code: { file: 'python', section: 4, source: 'ontology-lumen' },
    },
    {
      n: 3,
      highlight: ['definitions', 'e-definitions-permitted-actions', 'permitted-actions', 'e-governed-permitted-actions', 'governed', 'e-actions-section-governed', 'actions-section'],
      caption:
        'Governed action: the ontology also says which role may take which action, and on what condition. Every role may read; a write needs two yeses, the person\'s and the lumen-ops server\'s, and the supervisor\'s also needs a passed review. In the code: section 6.',
      code: { file: 'python', section: 6, source: 'ontology-lumen' },
    },
    {
      n: 4,
      highlight: ['actions-section', 'e-actions-section-governed', 'governed', 'e-governed-permitted-actions', 'permitted-actions'],
      caption:
        'A rule nobody checks is a wish. governance.py reads every recorded agents run and judges each action against section 6: 577 events in 104 desk runs, 0 not permitted, 60 conditional. The 60 are the write attempts, each refused because a person said no; no write landed. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'ontology-governance' },
    },
    {
      n: 5,
      highlight: ['data', 'e-data-definitions', 'definitions', 'e-definitions-permitted-actions', 'permitted-actions'],
      caption:
        'Each level stands on the one below. Definitions need data to describe, and a permission names roles and actions the ontology has defined. Grounding settles what something means; governance settles what an agent may do.',
    },
  ],
};
