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
  'LLM judge':
    'A model call that grades another model’s output against written criteria, used where no exact right answer exists to compare with, such as a reply’s tone.',
  rubric:
    'The written criteria a grader checks an output against; in this lab, three pass-or-fail questions about a reply draft.',
  agreement:
    'The share of cases on which two raters, such as an LLM judge and a person, give the same verdict.',
  "Cohen's kappa":
    'Agreement between two raters corrected for chance: (observed − chance) / (1 − chance), where 1 is perfect, 0 is no better than chance and below 0 is worse.',
  'position bias':
    'A model’s tendency to answer differently depending on where things sit in its prompt, such as whether a rubric comes before or after the text it grades.',
  'few-shot':
    'Prompting with a few worked examples of inputs and their right outputs placed in the prompt itself.',
  effort:
    'A request setting, output_config.effort (low, medium, high, xhigh or max), for how much the model thinks before it answers; the thinking is billed as output tokens.',
  weights:
    'The numbers inside a model that turn an input into an output; training changes them, a prompt does not.',
  'fine-tuning':
    'Training an existing model further on your own examples, so its weights change and the new behaviour no longer has to be written into every prompt.',
  LoRA:
    'Low-rank adaptation: fine-tuning that freezes the model and trains two thin matrices A (d × r) and B (r × d) beside a weight matrix, whose product is added to it.',
  'eval gate':
    'A check that runs an eval on a change and fails, with a non-zero exit code, when the score falls below a minimum or further below a baseline than the noise band.',
  feature:
    'One number a model reads about an input; here, the TF-IDF weight of one vocabulary word in a complaint.',
  'time split':
    'Dividing data by date, training on earlier records and testing on later ones, so the test imitates scoring the future.',
  'data leakage':
    'Information from the test data, or from the future, reaching training, so the test no longer measures how the model will do on new data, and often flatters it.',
  tokenizer:
    'The fixed rules that split text into tokens (here, lowercase words); training, serving and the browser must apply the same rules.',
  'TF-IDF':
    'Term frequency times inverse document frequency: a word’s count in a text, weighted up when few training texts contain it.',
  'Naive Bayes':
    'A classifier that scores each category by its log prior plus the log probability of every word given that category, treating words as independent.',
  'logistic regression':
    'A classifier that learns one weight per category and feature plus an intercept, and turns the weighted sums into probabilities with softmax.',
  overfitting:
    'Fitting the training data’s details instead of the general pattern, seen as a score that is clearly higher on training data than on new data.',
  precision:
    'Of the items a model assigned to a category, the share that really belong to it.',
  recall:
    'Of the items that really belong to a category, the share the model assigned to it.',
  F1:
    'The harmonic mean of precision and recall, 2PR / (P + R): high only when both are high.',
  'macro average':
    'The plain mean of a metric over categories, so a small category counts as much as a large one.',
  'experiment tracking':
    'Recording every training run’s settings, data version, code version, metrics and model file hash, so any result can be compared, explained and rebuilt later.',
  'model registry':
    'The list of a model’s registered versions, each with its file, hash and stage (staging, production or archived), plus a history of every move; servers load the version it names production.',
  drift:
    'A change, after a model is deployed, in the data it receives or in what its labels mean, compared with the data it was built and tested on.',
  PSI:
    'Population Stability Index: the sum over buckets of (a − e) × ln(a / e), where e and a are a reference period’s and a new period’s proportions; 0 when they match, larger as they move apart.',
  'promotion gate':
    'The check a candidate model must pass before it replaces production; here, beat production’s macro-F1 by more than the noise band on data neither model trained on, with no category’s F1 below a floor.',
  rollback:
    'Making the previous production version production again, as a recorded registry move, when a promoted model turns out worse.',
  'eval framework':
    'A library that standardises the parts of an eval: the dataset format, how a model is called, the scorers, repeats and the log. You supply the data, prompts and graders.',
  solver:
    'In Inspect, the step that builds the request for one sample and calls the model. The lab’s section 4 is a solver.',
  scorer:
    'In Inspect, the function that grades one sample’s answer. Its metrics, such as accuracy and standard error, combine the grades into a score.',
  epoch:
    'One pass of an Inspect eval over every sample. Running three epochs repeats each sample three times, as the lab’s three reps do.',
  MCP:
    'The Model Context Protocol: an open standard for how a program asks a server for tools, data and prompts, using JSON-RPC messages.',
  host:
    'In MCP, the program the person uses and the one that owns the model, such as a chat app or an agent. It holds the user’s consent and creates one client for each server.',
  'MCP client':
    'The part of a host that talks to one MCP server. It sends the requests and returns the results to the host.',
  'MCP server':
    'A program that offers tools, resources and prompts over MCP and answers requests. It never talks to the model.',
  'JSON-RPC':
    'The message format MCP uses: a request has an id, a method and params, and its result comes back with the same id.',
  stdio:
    'An MCP transport where the client starts the server as a child process and the two exchange one JSON message per line on standard input and output.',
  'Streamable HTTP':
    'An MCP transport where the server listens at one URL and the client POSTs each request, so a server can be shared or run on another machine.',
  'MCP tool':
    'A function a server offers that the model can ask for by name. It has a description, an input schema, an optional output schema and annotations.',
  'structured content':
    'The JSON value in a tool result that matches the tool’s output schema, sent beside the text blocks.',
  'MCP resource':
    'Data a server offers at an address (a URI) that the host reads. The application decides when to read it, not the model.',
  'resource template':
    'A resource address with a hole in it, such as lumen://tickets/{id}, which stands for many resources.',
  'MCP prompt':
    'A template a server offers for a person to pick, which returns messages to start a conversation.',
  discovery:
    'How an MCP client learns what a server has: it lists the tools, resources, templates and prompts and reads the server’s name and instructions.',
  InputRequiredResult:
    'A reply to a call that means the server needs an answer first. The client asks, then sends the same call again with the answer attached.',
  recording:
    'A file of real model responses, one per call, keyed by a hash of the request, so a run can be replayed for free and give the same answer.',
  'DNS rebinding':
    'An attack where a web page’s domain is made to point at 127.0.0.1, so the browser sends the page’s requests to a server on your own machine, with the page’s own name as Host and Origin. Checking each header against a fixed list of local names defeats it; comparing them with each other does not.',
  'bearer token':
    'A token sent in an Authorization: Bearer header. Whoever holds it can use it, so it is kept out of URLs and logs and is only good for one server.',
  elicitation:
    'An MCP server asking the user a question through the client, with a small form for the answer. lumen-ops uses it to ask for a yes before it writes.',
  provenance:
    'A record of where a piece of data came from: here, who wrote a label, through which client, with which model, and whether a yes was claimed.',
  'authorization server':
    'The OAuth server that issues tokens after checking who the user is. An MCP server only checks tokens; it never issues them.',
  'protected resource metadata':
    'A small JSON document an MCP server publishes (RFC 9728) that names its authorization server and its scopes, so a client can find where to get a token.',
  PKCE:
    'Proof Key for Code Exchange: the client sends a hash of a random secret when it asks for a code, and the secret itself when it swaps the code for a token, so a stolen code is useless.',
  audience:
    'The server a token was issued for, named in its aud claim (RFC 8707). A server refuses a token whose audience is not its own URI.',
  'OAuth scope':
    'A named permission a token carries, such as tickets:read or tickets:write. A server answers 403 to a token for it that lacks the scope it needs.',
  'MCP App':
    'An HTML page a server ships with a tool, as a ui:// resource. A host that supports the Apps extension shows it in a sandboxed frame beside the call.',
  'prompt injection':
    'Instructions hidden in data a model reads, such as a ticket body or a web page, written in the hope that the model obeys them instead of its user.',
  workflow:
    'An agentic system whose path is written in code: the code decides which model calls and tools run, in what order, and the model fills in each step.',
  agent:
    'An agentic system in which the model decides its own path: which tool to call next and when it is done, in a loop the code caps.',
  'prompt chaining':
    'A workflow pattern: a fixed sequence of model calls, each taking the previous one’s output, with checks in code between the steps.',
  routing:
    'A workflow pattern: one call sorts the input into one of a closed set of routes, and a prompt written for that route handles it.',
  parallelization:
    'A workflow pattern: model calls that do not depend on each other run at the same time (sectioning), or one task runs several times and the answers are compared (voting).',
  'orchestrator-workers':
    'A workflow pattern: a lead call plans the subtasks at run time, a worker does each one, and a last call combines their results.',
  'evaluator-optimizer':
    'A workflow pattern: one call drafts, a second call judges the draft against written criteria, and the draft is revised with the judge’s reasons, for a capped number of rounds.',
  'code gate':
    'A check in plain code between model calls, such as "the category must be one of six", that stops a run before a bad answer reaches the next step.',
  'multi-agent system':
    'A system that splits a job between several agents, each with its own prompt and tools, coordinated by code or by another agent.',
  supervisor:
    'An agent whose tools are other agents: it hands tasks to worker agents, reads their results, and decides what happens next.',
  handoff:
    'One agent passing a task to another. In the Lumen desk it is a tool call, handoff(worker, task), whose result is the worker’s answer.',
  'shared state':
    'The small, explicit record that a supervisor and its workers read and update, such as the category, the draft and the review, with every change logged.',
  'record/replay proxy':
    'A local server between an agent and a model API. It answers each request from a saved recording (replay) or, only when asked and approved, forwards it and saves the reply (record).',
  'replay key':
    'The name a recording is filed under: a hash of the request with the parts that change on every run removed, so the same request always finds the same recording.',
  'agent framework':
    'A library that supplies the agent loop, tool calling and state, such as LangGraph or the Claude Agent SDK, so your code describes the agent and the library runs it.',
  'human-in-the-loop':
    'A pause in an agent run where a person must approve or reject an action before it runs.',
  subagent:
    'An agent that another agent starts for one task, with its own prompt and tools. It hands its result back to the agent that started it.',
  hook:
    'Your code that a framework calls at a fixed point in a run, such as just before a tool runs. It can let the call through, change it or refuse it.',
  trajectory:
    'The path an agent took in one run: which tools and agents it called, in what order, and with which arguments.',
  'data warehouse':
    'One central store that data from many sources is copied into, in one agreed schema, usually run by one team. Readers query the copy.',
  'data mesh':
    'A way to organise data ownership: each domain team owns its datasets as products and answers for them to the teams that read them. There is no central copy.',
  'data fabric':
    'A layer of metadata over data that stays where its owners keep it: a catalogue of what exists, lineage of who reads what, and checks that run in place.',
  lineage:
    'The record of which producer writes each dataset, which consumer reads it, and which dataset is derived from which, kept as a graph.',
  'data contract':
    'A file in which the producer of a dataset states what it promises its consumers: the shape and meaning of each field, quality, freshness, the owner and the readers.',
  producer:
    'The team or track that writes a dataset and answers for it. In this course, the owner named in a contract.',
  consumer:
    'A program or team that reads a dataset. A contract names each consumer and the version it declares.',
  'Open Data Contract Standard':
    'An open YAML format for data contracts (ODCS), maintained by the Bitol project under the Linux Foundation. The contracts in this course borrow its shape.',
  sunset:
    'The date by which readers of a deprecated contract version must have moved to the new one. Until then the old version stays valid.',
  'data catalogue':
    'A list of datasets with what a reader needs before opening one: where it lives, its size, its fields, who writes it, who reads it and when it last changed.',
  'schema sketch':
    'The field names and JSON types read from a sample of a dataset’s rows. It describes the data as it is; a contract promises what it will be.',
  verdict:
    'One rule’s result for one field or for the whole dataset: the rule, the field, ok or not, and a detail. Both validators print one line per verdict.',
  'composite key':
    'A key made of several fields together. With the key desk and ticket, two rows repeat only when both fields match.',
  freshness:
    'How old a dataset is, measured from when it was last written. A contract promises a maximum age.',
  'breaking change':
    'A change that makes an existing reader fail, such as renaming, removing or retyping a field. It needs a new major version.',
  'twin validator':
    'A second implementation of the same validator in another language, held to print identical verdicts by shared fixtures and a byte-for-byte test.',
  shim:
    'A thin piece of code at the edge that translates data from a new shape to the shape an unchanged reader expects.',
  'consumer gate':
    'A check that passes only when every consumer is green on the contract version it declares. In this course, gate.py.',
  taxonomy:
    'A controlled vocabulary: the names of things, perhaps sorted into a hierarchy, with nothing about what they mean or how they relate.',
  schema:
    'A description of a record’s fields and their types, and which are required. It says what shape the data has, not what it means.',
  ontology:
    'A shared description of a domain’s meaning: classes with definitions, their properties and relations, named individuals and the rules that hold between them.',
  'knowledge graph':
    'The facts themselves, stored as things and the typed links between them. An ontology says which kinds of thing and link may exist; the graph holds the ones that do.',
  RDFS:
    'RDF Schema, a W3C standard for describing classes and properties in RDF, including subclasses and each property’s domain and range.',
  OWL:
    'The Web Ontology Language, a W3C standard for ontologies that adds richer class definitions, cardinality and logic a reasoner can infer from.',
  SKOS:
    'Simple Knowledge Organization System, a W3C standard for vocabularies: concepts with preferred labels, definitions and broader or narrower links.',
  SHACL:
    'Shapes Constraint Language, a W3C standard for the rules data must meet, written as shapes a validator checks a graph against.',
  individual:
    'A named member of a class in an ontology, such as the category outage or the priority urgent, with its own definition.',
  'checkable constraint':
    'An ontology rule written as an expression over a record’s fields, so a program can evaluate it on every record.',
  'manual constraint':
    'An ontology rule that needs a person to read the record, such as one about amounts or user counts that are only in the text. It is listed, never pretended to be checked.',
  grounding:
    'Tying an AI system to a source of meaning: its prompts, schemas and tools come from it, and its outputs are checked against it.',
  'single source of truth':
    'The one place a fact is written. Every other artefact that states the fact is generated from it or audited against it.',
  'inferred context':
    'Meaning an agent works out at run time from the data and what describes it, such as a schema or column names, with no definition written down.',
  'governed action':
    'An agent action that a written rule permits, names a role and a condition for, and that code enforces: who may do what, and when.',
  gateway:
    'One HTTP server that every program calls instead of the model API; it forwards each call and does routing, retries, rate limits, caching, budgets and tracing for all of them.',
  'token bucket':
    'A rate limiter that holds a budget of requests or tokens, refilled at a steady rate; a call takes from it, and waits or is refused when too little is left.',
  'response cache':
    'A store of whole answers, kept by the caller or a gateway, that answers a repeat of an identical request without calling the model at all.',
  'prompt cache':
    'The model API’s own cache of a request’s repeated prefix: the call is still made, but input read from the cache is billed at a fraction of the usual price.',
} as const satisfies Record<string, string>;

export type GlossaryKey = keyof typeof glossary;
