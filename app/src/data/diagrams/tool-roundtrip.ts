import type { Diagram } from '../../lib/diagram';

// Layout (nodes 140x56; gaps sized to fit the edge labels). Row 1 left to right: model, harness, registry,
// function. Row 2 runs back right to left: the tool_result node, then the model.
export const toolRoundtrip: Diagram = {
  id: 'tool-roundtrip',
  title: 'One tool round trip',
  width: 900,
  height: 280,
  nodes: [
    { id: 'model', label: 'Model', role: 'model', x: 30, y: 40, w: 140 },
    { id: 'harness', label: 'Harness', role: 'harness', x: 260, y: 40, w: 140 },
    { id: 'registry', label: 'Tool registry', role: 'harness', x: 510, y: 40, w: 140 },
    { id: 'function', label: 'Your function', role: 'tool', x: 740, y: 40, w: 140 },
    { id: 'tool-result', label: 'tool_result', role: 'memory', x: 260, y: 190, w: 140 },
  ],
  edges: [
    { id: 'e-model-harness', from: 'model', to: 'harness', label: 'tool_use' },
    { id: 'e-harness-registry', from: 'harness', to: 'registry', label: 'look up name' },
    { id: 'e-registry-function', from: 'registry', to: 'function', label: 'call' },
    { id: 'e-function-result', from: 'function', to: 'tool-result', label: 'return value' },
    { id: 'e-result-model', from: 'tool-result', to: 'model', label: 'same id' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['model', 'e-model-harness', 'harness'],
      caption: 'The model does not run anything. It emits a tool_use block with a name, an input, and an id.',
      code: { file: 'python', section: 6 },
    },
    {
      n: 2,
      highlight: ['harness', 'e-harness-registry', 'registry'],
      caption: 'The harness looks the name up in the tool registry, a dict of names to functions and schemas.',
      code: { file: 'python', section: 3 },
    },
    {
      n: 3,
      highlight: ['registry', 'e-registry-function', 'function'],
      caption: 'The harness calls your function with the model\'s input. Your code runs, not the model.',
      code: { file: 'python', section: 5 },
    },
    {
      n: 4,
      highlight: ['function', 'e-function-result', 'tool-result'],
      caption: 'The return value is wrapped in a tool_result block. Its tool_use_id must match the id from step 1.',
      code: { file: 'python', section: 8 },
    },
    {
      n: 5,
      highlight: ['tool-result', 'e-result-model', 'model'],
      caption: 'The tool_result goes back in the next request. The model reads it and continues.',
      code: { file: 'python', section: 8 },
    },
  ],
};
