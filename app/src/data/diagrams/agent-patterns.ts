import type { Diagram } from '../../lib/diagram';

// The five patterns of Anthropic's "Building effective agents", and the agent loop beside them.
// Layout: the ticket on the left; above, the workflow node fans out to the five patterns stacked in
// one column (210 wide), each joined to one tall model node on the right by a straight line; below,
// the agent and its tools trade calls in a loop. Each linked caption ends with the section it links
// to (the test pins the number against the step's code link): patterns.py / patterns.go sections
// 2-6, and supervisor.py section 2 for the single agent.
export const agentPatterns: Diagram = {
  id: 'agent-patterns',
  title: 'Workflows or agents: the five patterns and the agent loop',
  width: 1170,
  height: 520,
  nodes: [
    { id: 'ticket', label: 'Ticket', role: 'user', x: 30, y: 300 },
    { id: 'workflow', label: 'Workflow: code decides', role: 'harness', x: 250, y: 170, w: 200 },
    { id: 'chain', label: 'Prompt chaining', role: 'harness', x: 540, y: 20, w: 210 },
    { id: 'route', label: 'Routing', role: 'harness', x: 540, y: 95, w: 210 },
    { id: 'parallel', label: 'Parallelization', role: 'harness', x: 540, y: 170, w: 210 },
    { id: 'orchestrate', label: 'Orchestrator-workers', role: 'harness', x: 540, y: 245, w: 210 },
    { id: 'evaluate', label: 'Evaluator-optimizer', role: 'harness', x: 540, y: 320, w: 210 },
    { id: 'model', label: 'Claude (model)', role: 'model', x: 940, y: 20, w: 200, h: 356 },
    { id: 'agent', label: 'Agent: model decides', role: 'model', x: 540, y: 440, w: 210 },
    { id: 'tools', label: 'Tools', role: 'tool', x: 940, y: 440, w: 200 },
  ],
  edges: [
    { id: 'e-ticket-workflow', from: 'ticket', to: 'workflow' },
    { id: 'e-ticket-agent', from: 'ticket', to: 'agent' },
    { id: 'e-workflow-chain', from: 'workflow', to: 'chain' },
    { id: 'e-workflow-route', from: 'workflow', to: 'route' },
    { id: 'e-workflow-parallel', from: 'workflow', to: 'parallel' },
    { id: 'e-workflow-orchestrate', from: 'workflow', to: 'orchestrate' },
    { id: 'e-workflow-evaluate', from: 'workflow', to: 'evaluate' },
    { id: 'e-chain-model', from: 'chain', to: 'model', label: 'one after another' },
    { id: 'e-route-model', from: 'route', to: 'model', label: 'pick, then reply' },
    { id: 'e-parallel-model', from: 'parallel', to: 'model', label: 'side by side' },
    { id: 'e-orchestrate-model', from: 'orchestrate', to: 'model', label: 'plan, work, merge' },
    { id: 'e-evaluate-model', from: 'evaluate', to: 'model', label: 'draft, judge, revise' },
    { id: 'e-agent-tools', from: 'agent', to: 'tools', label: 'tool_use' },
    { id: 'e-tools-agent', from: 'tools', to: 'agent', label: 'tool_result' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['ticket', 'e-ticket-workflow', 'workflow', 'e-ticket-agent', 'agent'],
      caption:
        'Anthropic’s "Building effective agents" splits agentic systems in two. In a workflow, code decides the path and the model fills in each step. In an agent, the model decides the path, one tool call at a time. Start with the simplest one that does the job.',
    },
    {
      n: 2,
      highlight: ['workflow', 'e-workflow-chain', 'chain', 'e-chain-model', 'model'],
      caption:
        'Prompt chaining: a fixed sequence of calls with a code gate between them. Classify the ticket, check the category is one of six, then write the reply for it. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'agents-patterns' },
    },
    {
      n: 3,
      highlight: ['workflow', 'e-workflow-route', 'route', 'e-route-model', 'model'],
      caption:
        'Routing: one cheap call picks one of four routes (billing, bug, account, other), and that route’s specialist prompt writes the reply. The route is a closed set the code checks. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'agents-patterns' },
    },
    {
      n: 4,
      highlight: ['workflow', 'e-workflow-parallel', 'parallel', 'e-parallel-model', 'model'],
      caption:
        'Parallelization: three lookups that do not depend on each other run side by side, then one call summarizes what they found. In the code: section 4.',
      code: { file: 'python', section: 4, source: 'agents-patterns' },
    },
    {
      n: 5,
      highlight: ['workflow', 'e-workflow-orchestrate', 'orchestrate', 'e-orchestrate-model', 'model'],
      caption:
        'Orchestrator-workers: a lead call plans up to three subtasks at run time, a worker does each with read-only tools, and a last call writes the reply. In the code: section 5.',
      code: { file: 'python', section: 5, source: 'agents-patterns' },
    },
    {
      n: 6,
      highlight: ['workflow', 'e-workflow-evaluate', 'evaluate', 'e-evaluate-model', 'model'],
      caption:
        'Evaluator-optimizer: draft a reply, let a judge check it against the LLMs track’s rubric, and revise with the judge’s reasons. At most three judge calls. In the code: section 6.',
      code: { file: 'python', section: 6, source: 'agents-patterns' },
    },
    {
      n: 7,
      highlight: ['ticket', 'e-ticket-agent', 'agent', 'e-agent-tools', 'tools', 'e-tools-agent'],
      caption:
        'The agent: the model decides which tool to call next and when to stop, in a loop the code caps. It is flexible, and it costs more calls and is harder to predict. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'agents-supervisor' },
    },
  ],
};
