/** Definitions shown by <Term t="…">. Keys are the terms as written in prose. */
export const glossary = {
  harness:
    'The ordinary program around a model: it sends requests, runs the tools the model asks for, feeds results back and decides when to stop.',
  token:
    'The unit a model reads and writes; roughly three quarters of an English word. Limits and prices are counted in tokens.',
  'context window':
    'The maximum number of tokens a model can consider in one request: system prompt, every message so far and the reply.',
  'system prompt':
    'Standing instructions sent with every request, separate from the conversation, that set the model’s role and rules.',
  stop_reason:
    'The field on a model response that says why it stopped: finished its turn, hit max_tokens, or wants to use a tool.',
  tool_use:
    'A content block in the model’s reply asking the harness to run a named tool with JSON input.',
  tool_result:
    'A content block the harness sends back in the next user message, carrying a tool’s output for the matching tool_use id.',
  'JSON Schema':
    'A standard way to describe the shape of JSON data; each tool declares its input this way so the model knows what to send.',
  compaction:
    'Replacing a long conversation with a summary and starting a fresh one, so the work continues within the context budget.',
  'API key':
    'A secret string that identifies your account to the API. Requests without a valid key are rejected; usage is billed to the key’s account.',
  messages:
    'The conversation history you send with every request: a list of turns, each with a role (user or assistant) and content.',
  role:
    'Who wrote a message: "user" for anything the harness sends (your task, tool results), "assistant" for the model’s replies.',
  'content block':
    'One typed piece of a message’s content, such as a text block or a tool_use block. A reply can hold several.',
  max_tokens:
    'The request parameter that caps how many tokens the model may write in one reply. Also a stop_reason when the cap is hit.',
  usage:
    'The field on a response that counts input_tokens (what you sent) and output_tokens (what the model wrote). Billing uses these numbers.',
  'tool registry':
    'The harness’s table of tools, keyed by name. Each entry holds the schema the model sees and the function the harness runs.',
  goroutine:
    'A lightweight thread managed by the Go runtime, started with the go keyword; used to run work concurrently.',
  'agent loop':
    'The harness’s main loop: call the model, run any tools it asked for, send the results back, and repeat until it stops asking.',
  tool_use_id:
    'The field on a tool_result that copies the id of the tool_use block it answers, so the model can match each output to its request.',
  'permission gate':
    'The harness step that holds a risky tool call until a person approves or denies it.',
  sandbox:
    'A limit on what tools can reach. In this harness it is a path rule: every file path must resolve inside the workspace folder.',
  'thread pool':
    'A fixed set of worker threads that take jobs from a queue. Python’s ThreadPoolExecutor is one; it reuses threads instead of starting one per job.',
  WaitGroup:
    'A Go counter from the sync package: Add before starting a goroutine, Done when it finishes, Wait blocks until the count is back to zero.',
  'rate limit':
    'A cap on how many requests or tokens an account may use per minute. A request over it gets HTTP 429 and should be retried after a short wait.',
  'JSON Lines':
    'A text format with one complete JSON object per line. Each line can be appended and read on its own, which suits logs.',
  eval:
    'A repeatable test of a model feature: run it on a fixed set of inputs with known right answers, grade every output, and report the share right.',
  grader:
    'The code or model that turns one output into a grade, such as right or wrong, by checking it against the label or a rubric.',
  'dev set':
    'The part of an eval set you tune on: read its mistakes, change the prompt and rerun as often as you like.',
  'test set':
    'The part of an eval set held back from tuning and read only to report a final score, so that score stays honest.',
  'edge case':
    'An input that is unusual or hard on purpose, such as a sarcastic, very short or non-English ticket; tagging them lets you score them on their own.',
  'labelling guide':
    'Written rules, applied in order, that decide each input’s right answer, so two labellers working alone reach the same label.',
  baseline:
    'The score a trivial method gets, such as always answering the most common label; a real score is judged by how far it beats this.',
  'structured output':
    'An API feature that makes the model’s reply follow a JSON Schema you send, so when the model finishes normally the answer parses and only uses allowed values.',
  accuracy:
    'The share of graded cases a model got right, from 0 to 1, usually shown as a percentage.',
  'confusion matrix':
    'A table that counts each pair of right answer and model answer; the diagonal holds the right answers and every other cell is one kind of mistake.',
  'cache key':
    'A hash of everything that shapes a model call (model, prompt text, input, repeat), used to find its saved answer; change any part and the key changes.',
  'noise band':
    'How far a score can move by luck alone, roughly 1/√m for m scored cases; a difference smaller than the band is not evidence.',
} as const satisfies Record<string, string>;

export type GlossaryKey = keyof typeof glossary;
