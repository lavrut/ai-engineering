import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1 runs left to right: Tickets, Task prompt,
// Model, Saved response. Row 2 snakes back right to left: Code grader under
// Saved response, then Score and Report. The LLM judge sits to the right of
// row 2; its verdicts run along a lane below row 2 into the bottom of Score,
// so they never cross the code grader's path. Code links point at the LLMs
// lab (llms/python/triage_lab.py and llms/go/main.go).
export const evalPipeline: Diagram = {
  id: 'eval-pipeline',
  title: 'One eval run, step by step',
  width: 1140,
  height: 380,
  nodes: [
    { id: 'dataset', label: 'Tickets (dataset)', role: 'dataset', x: 30, y: 60 },
    { id: 'prompt', label: 'Task prompt', role: 'memory', x: 270, y: 60 },
    { id: 'model', label: 'Model (Haiku)', role: 'model', x: 510, y: 60, w: 170 },
    { id: 'saved', label: 'Saved response', role: 'logs', x: 770, y: 60, w: 170 },
    { id: 'judge', label: 'LLM judge (Opus)', role: 'grader', x: 960, y: 230, w: 160 },
    { id: 'grader', label: 'Code grader', role: 'grader', x: 770, y: 230, w: 170 },
    { id: 'score', label: 'Score', role: 'harness', x: 510, y: 230, w: 170 },
    { id: 'report', label: 'Report', role: 'harness', x: 270, y: 230 },
  ],
  edges: [
    { id: 'e-dataset-prompt', from: 'dataset', to: 'prompt', label: 'ticket' },
    { id: 'e-prompt-model', from: 'prompt', to: 'model', label: 'request' },
    { id: 'e-model-saved', from: 'model', to: 'saved', label: 'answer' },
    { id: 'e-saved-grader', from: 'saved', to: 'grader', label: 'category, priority' },
    { id: 'e-saved-judge', from: 'saved', to: 'judge', label: 'reply' },
    { id: 'e-grader-score', from: 'grader', to: 'score', label: 'grades' },
    {
      id: 'e-judge-score',
      from: 'judge',
      to: 'score',
      label: 'verdicts',
      via: [
        [1040, 340],
        [595, 340],
      ],
    },
    { id: 'e-score-report', from: 'score', to: 'report', label: 'scores' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['dataset', 'e-dataset-prompt'],
      caption:
        'The dataset is a file of support tickets. Each one has the right category and priority, and a split: tune on dev, and use test only for the final number.',
      code: { file: 'python', section: 2, source: 'llms' },
    },
    {
      n: 2,
      highlight: ['dataset', 'e-dataset-prompt', 'prompt'],
      caption:
        'Each ticket goes into the task prompt: one system prompt version plus the ticket text, with a JSON schema that fixes the shape of the answer.',
      code: { file: 'python', section: 3, source: 'llms' },
    },
    {
      n: 3,
      highlight: ['prompt', 'e-prompt-model', 'model'],
      caption: 'The model answers with a category, a priority and a reply draft. Structured output keeps the answer valid JSON.',
      code: { file: 'python', section: 4, source: 'llms' },
    },
    {
      n: 4,
      highlight: ['model', 'e-model-saved', 'saved'],
      caption:
        'Every answer is saved under a key: a hash of the model, effort, prompt, ticket and repeat. Replay reads it back, so a rerun is free and gives the same numbers.',
      code: { file: 'python', section: 4, source: 'llms' },
    },
    {
      n: 5,
      highlight: ['saved', 'e-saved-grader', 'grader'],
      caption:
        'Code grades the category and the priority: an exact match with the label, free and the same every time. A refusal counts as wrong; an error is left out.',
      code: { file: 'python', section: 5, source: 'llms' },
    },
    {
      n: 6,
      highlight: ['saved', 'e-saved-judge', 'judge'],
      caption:
        'A reply draft has no single right answer, so a stronger model grades it against a rubric, with a pass or fail and a reason for each item.',
      code: { file: 'python', section: 6, source: 'llms' },
    },
    {
      n: 7,
      highlight: ['grader', 'e-grader-score', 'judge', 'e-judge-score', 'score'],
      caption:
        'The grades become scores: the share of tickets right on each field, and a noise band of about 1/sqrt(n). A difference smaller than the band is not evidence.',
      code: { file: 'python', section: 9, source: 'llms' },
    },
    {
      n: 8,
      highlight: ['score', 'e-score-report', 'report'],
      caption:
        'The command line prints the report: cases, scored lines, each accuracy and the band. compare and gate read the same numbers.',
      code: { file: 'python', section: 10, source: 'llms' },
    },
  ],
};
