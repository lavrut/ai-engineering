import type { Diagram } from '../../lib/diagram';

// One row, left to right: dataset, solver, replay provider, scorers, log. Code links point at
// llms/inspect/triage_inspect.py (source 'llms-inspect'); each caption names the matching lab
// section in llms/python/triage_lab.py.
export const inspectPipeline: Diagram = {
  id: 'inspect-pipeline',
  title: 'The lab, rebuilt in Inspect',
  width: 1140,
  height: 160,
  nodes: [
    { id: 'dataset', label: 'Tickets (dataset)', role: 'dataset', x: 30, y: 50 },
    { id: 'solver', label: 'Solver', role: 'harness', x: 250, y: 50 },
    { id: 'replay', label: 'Model (replay provider)', role: 'model', x: 470, y: 50, w: 210 },
    { id: 'scorers', label: 'Scorers', role: 'grader', x: 740, y: 50 },
    { id: 'log', label: 'Log and metrics', role: 'logs', x: 950, y: 50, w: 160 },
  ],
  edges: [
    { id: 'e-dataset-solver', from: 'dataset', to: 'solver', label: 'sample' },
    { id: 'e-solver-replay', from: 'solver', to: 'replay', label: 'request' },
    { id: 'e-replay-scorers', from: 'replay', to: 'scorers', label: 'answer' },
    { id: 'e-scorers-log', from: 'scorers', to: 'log', label: 'scores' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['dataset', 'e-dataset-solver'],
      caption:
        'Each ticket becomes an Inspect sample: the text as input, the right category as target, and the priority, split and tags as metadata. In the lab: section 2.',
      code: { file: 'python', section: 2, source: 'llms-inspect' },
    },
    {
      n: 2,
      highlight: ['dataset', 'e-dataset-solver', 'solver', 'e-solver-replay'],
      caption:
        'The solver builds the request: the chosen system prompt plus the ticket, and the repeat number in a header. It is the same prompt the lab sends. In the lab: sections 3 and 4 (the schema in 3, the request in 4).',
      code: { file: 'python', section: 4, source: 'llms-inspect' },
    },
    {
      n: 3,
      highlight: ['solver', 'e-solver-replay', 'replay', 'e-replay-scorers'],
      caption:
        'A replay provider stands in for the model. It looks the request up in the lab’s saved answers and returns the same text, so nothing is spent. A miss is an error, never a paid call. In the lab: section 4.',
      code: { file: 'python', section: 3, source: 'llms-inspect' },
    },
    {
      n: 4,
      highlight: ['replay', 'e-replay-scorers', 'scorers', 'e-scorers-log'],
      caption:
        'Code scorers grade the category and the priority by exact match. The judge scorer, covered in section 6, grades the reply against the rubric. In the lab: section 5.',
      code: { file: 'python', section: 5, source: 'llms-inspect' },
    },
    {
      n: 5,
      highlight: ['scorers', 'e-scorers-log', 'log'],
      caption:
        'A task ties the dataset, solver and scorers together. Running it writes a log with accuracy and its standard error, which inspect view can open. In the lab: sections 8 and 9.',
      code: { file: 'python', section: 7, source: 'llms-inspect' },
    },
  ],
};
