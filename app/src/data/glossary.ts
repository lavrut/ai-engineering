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
} as const satisfies Record<string, string>;

export type GlossaryKey = keyof typeof glossary;
