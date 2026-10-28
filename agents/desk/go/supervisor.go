package patterns

// The single-agent desk and the multi-agent supervisor desk, hand-built. This file mirrors
// agents/desk/python/supervisor.py section for section, and agents/fixtures/contract.jsonl holds the two
// to the same behaviour.
//
// Both run one ticket and return a DeskRun: Output (SingleOutput or SupervisorOutput, language-neutral
// JSON once marshalled), Calls (the model-call sequence as {agent, role, tools}: the tool names that call
// offered, sorted) and Trace (the handoff records; the model and tool records come from the Model and
// Tools of Task 8).
//
// The untrusted-read rule: the ticket text is in the first message, so the context holds untrusted text
// from the first call. The desks take DeskTools, whose MarkUntrusted they call before the first call (R24,
// R28: required, so a Tools without the flag cannot be passed), and then every write needs a person's yes
// before the server's own confirmation. That also closes the MCP track's
// same-response boundary (R19) for these desks: there is no "before the first read". Tool calls run one at
// a time, in the order the model asked for them.

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"slices"
	"sort"
	"strings"
	"time"
)

// ── 1. Configuration ────────────────────────────────────────────────────────

const (
	MaxTurns = 8 // model calls one agent loop may spend (the single agent; the supervisor's own loop)
	// Stopped is the reply when the loop hits MaxTurns: the harness's words for the same stop.
	Stopped = "[stopped: reached MAX_TURNS without finishing]"
)

// Workers are the supervisor's four workers.
var Workers = []string{"triage", "research", "drafter", "reviewer"}

var workerRole = map[string]string{"triage": "triage", "research": "research", "drafter": "draft", "reviewer": "review"}

// Priorities are the LLMs lab's four.
var Priorities = []string{"low", "normal", "high", "urgent"}

// OpsTools is lumen-ops: the supervisor's alone, and only once the reviewer has passed the current draft.
var OpsTools = []string{"label_ticket"}

// HandoffSchema is the handoff tool as the Messages API takes it (a real Model resolves "handoff" to it).
var HandoffSchema = map[string]any{
	"name": "handoff",
	"description": "Hand one task to a worker and get its result back. Workers: triage (category and priority), " +
		"research (looks things up with the read tools), drafter (writes the reply to the customer), " +
		"reviewer (checks the current draft against the rubric).",
	"input_schema": map[string]any{"type": "object", "properties": map[string]any{
		"worker": map[string]any{"type": "string", "enum": Workers},
		"task":   map[string]any{"type": "string", "description": "What the worker should do, in one or two sentences."}},
		"required": []string{"worker", "task"}},
}

var singleSystem = "You are the Lumen support desk. Read what you need with the tools, label the ticket with " +
	"label_ticket if you are sure of its category, then answer with the reply to the customer: " +
	"two or three sentences. " + Rules

var supervisorSystem = "You supervise the Lumen support desk. Use the handoff tool to give work to your workers: " +
	"triage, then research if facts are needed, then drafter, then reviewer. If the reviewer " +
	"fails the draft, ask the drafter again with the reasons. label_ticket appears in your tools " +
	"only once the reviewer has passed the current draft; use it to label the ticket with its " +
	"category. When done, answer with one short line for the log. " + Rules

// DeskTools is the Tools the desks take: it also keeps the untrusted-read flag (the real MCP Tools of
// Task 8). The desks call MarkUntrusted before the first model call, because the ticket text is in the
// first message. It is required (R28): an optional interface would fail open.
type DeskTools interface {
	Tools
	MarkUntrusted()
}

// DeskCall is one entry of a desk's model-call sequence: who called, in which role, offered which tools.
type DeskCall struct {
	Agent string   `json:"agent"`
	Role  string   `json:"role"`
	Tools []string `json:"tools"`
}

// ToolCall is one MCP tool call and whether it failed (refused by the desk counts as failed).
type ToolCall struct {
	Agent   string         `json:"agent"`
	Tool    string         `json:"tool"`
	Args    map[string]any `json:"args"`
	IsError bool           `json:"is_error"`
}

// DeskError is a call to a name that is no desk MCP tool and no known worker (R35/R37): it never reaches
// the tools, is never a tool call or a handoff, and the model reads Result as an error result.
type DeskError struct {
	Agent  string         `json:"agent"`
	Tool   string         `json:"tool"`
	Args   map[string]any `json:"args"`
	Result string         `json:"result"`
}

// TraceRecord is the desk_core Trace shape (R2); a field the desk cannot fill is null.
type TraceRecord struct {
	Agent     string `json:"agent"`
	Kind      string `json:"kind"`
	Name      string `json:"name"`
	TokensIn  *int   `json:"tokens_in"`
	TokensOut *int   `json:"tokens_out"`
	Ms        *int   `json:"ms"`
}

// DeskRun is what a desk returns.
type DeskRun struct {
	Output any
	Calls  []DeskCall
	Trace  []TraceRecord
	sink   TraceSink // the Model, when it keeps the run's trace (Task 8); nil for a scripted one
}

// add appends a desk record (handoff or stop) to the run, and hands it to the Model's log when it keeps one,
// so that log holds every record in the order it happened.
func (run *DeskRun) add(r TraceRecord) {
	run.Trace = append(run.Trace, r)
	if run.sink != nil {
		run.sink.Record(r)
	}
}

// SingleOutput is the single agent's result.
type SingleOutput struct {
	Reply     string      `json:"reply"`
	Stop      string      `json:"stop"` // "end_turn" or "max_turns"
	ToolCalls []ToolCall  `json:"tool_calls"`
	Errors    []DeskError `json:"errors"`
}

// pyJSON is JSON for prompt text, written the way Python's json.dumps(v, ensure_ascii=False,
// sort_keys=True) writes it (R23): ", " and ": " separators, sorted keys, no HTML escaping. Known gap:
// Go escapes U+2028 and U+2029, which Python leaves as they are.
func pyJSON(v any) string {
	raw, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprint(v)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var g any
	if err := dec.Decode(&g); err != nil {
		return string(raw)
	}
	var b strings.Builder
	writePy(&b, g)
	return b.String()
}

func writePy(b *strings.Builder, v any) {
	switch x := v.(type) {
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		b.WriteString("{")
		for i, k := range keys {
			if i > 0 {
				b.WriteString(", ")
			}
			writePy(b, k)
			b.WriteString(": ")
			writePy(b, x[k])
		}
		b.WriteString("}")
	case []any:
		b.WriteString("[")
		for i, e := range x {
			if i > 0 {
				b.WriteString(", ")
			}
			writePy(b, e)
		}
		b.WriteString("]")
	case string:
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(x)
		b.WriteString(strings.TrimSuffix(buf.String(), "\n"))
	case json.Number:
		b.WriteString(x.String())
	case bool:
		b.WriteString(fmt.Sprint(x))
	case nil:
		b.WriteString("null")
	}
}

// PyJSON is pyJSON for the engine's own packages (the MCP Tools write structured results with it, R23).
func PyJSON(v any) string { return pyJSON(v) }

func intp(n int) *int { return &n }

// stop records a loop that hit its cap (R29): kind "stop", name "max_turns" or "max_worker_steps".
func stop(run *DeskRun, agent, limit string) {
	run.add(TraceRecord{Agent: agent, Kind: "stop", Name: limit})
}

// deskAsk is one model call, logged as {agent, role, tools} in call order.
func deskAsk(run *DeskRun, m Model, agent, role, system string, msgs []Message, tools []string) (Reply, error) {
	names := slices.Clone(tools)
	if names == nil {
		names = []string{}
	}
	slices.Sort(names)
	run.Calls = append(run.Calls, DeskCall{agent, role, names})
	var offered []string
	if len(names) > 0 {
		offered = names
	}
	r, err := m.Call(Request{Agent: agent, System: system, Messages: msgs, Tools: offered})
	if err != nil {
		return r, fmt.Errorf("%s: %w", agent, err)
	}
	return r, nil
}

// isDeskTool reports whether name is a lumen-read or lumen-ops tool.
func isDeskTool(name string) bool {
	return slices.Contains(ReadTools, name) || slices.Contains(OpsTools, name)
}

// deskTool runs one tool_use if allowed holds its name and refuse is empty; anything else is refused
// here and never reaches the tools. Logged in toolCalls when the name is a desk MCP tool, else in errs
// (R37); returns the tool_result block.
func deskTool(toolCalls *[]ToolCall, errs *[]DeskError, tools Tools, agent string, use Block, allowed []string, refuse string) (map[string]any, error) {
	args := use.Input
	if args == nil {
		args = map[string]any{}
	}
	out, isErr := "Unknown tool: "+use.Name, true
	if slices.Contains(allowed, use.Name) && refuse != "" {
		out = refuse
	} else if slices.Contains(allowed, use.Name) {
		var err error
		if out, isErr, err = tools.Run(use.Name, args); err != nil {
			return nil, err
		}
	}
	if isDeskTool(use.Name) {
		*toolCalls = append(*toolCalls, ToolCall{agent, use.Name, args, isErr})
	} else {
		*errs = append(*errs, DeskError{agent, use.Name, args, out})
	}
	return map[string]any{"type": "tool_result", "tool_use_id": use.ID, "content": out, "is_error": isErr}, nil
}

// ── 2. The single agent ─────────────────────────────────────────────────────

// SingleAgent is one agent with every desk tool (lumen-read and lumen-ops) and a plain tool loop capped
// at MaxTurns.
//
// Why: the simplest agent. The model decides the path; the only gates are the ones every write passes
// (the guard, then the server's confirmation). It is the baseline the supervisor must beat.
// Not when: one prompt holding every tool and every step gets confused, or a write must wait for a check
// the model could skip: here nothing but the gate stops a label before the reply is good.
func SingleAgent(t Ticket, m Model, tools DeskTools) (DeskRun, error) {
	run := DeskRun{}
	run.sink, _ = m.(TraceSink)
	if tools == nil {
		return run, fmt.Errorf("single: no tools")
	}
	tools.MarkUntrusted() // the ticket text is in the first message (file comment)
	all := append(slices.Clone(ReadTools), OpsTools...)
	slices.Sort(all)
	out := SingleOutput{Reply: Stopped, Stop: "max_turns", ToolCalls: []ToolCall{}, Errors: []DeskError{}}
	msgs := ticketMsg(t, "")
	for turn := range MaxTurns {
		r, err := deskAsk(&run, m, "single", "act", singleSystem, msgs, all)
		if err != nil {
			return run, err
		}
		u := uses(r)
		if len(u) == 0 {
			out.Reply, out.Stop = text(r), "end_turn"
			break
		}
		results := []map[string]any{}
		for _, use := range u { // in order, one at a time
			res, err := deskTool(&out.ToolCalls, &out.Errors, tools, "single", use, all, "")
			if err != nil {
				return run, err
			}
			results = append(results, res)
		}
		msgs = append(slices.Clone(msgs), Message{"assistant", blocksOf(r)}, Message{"user", results})
		if turn == MaxTurns-1 {
			stop(&run, "single", "max_turns")
		}
	}
	run.Output = out
	return run, nil
}

// ── 3. Shared state, and every change logged ────────────────────────────────

// Review is the reviewer's verdict on the current draft.
type Review struct {
	Passed  bool            `json:"passed"`
	Verdict map[string]bool `json:"verdict"`
}

// State is the supervisor's shared state; a field not yet set is null.
type State struct {
	TicketID string  `json:"ticket_id"`
	Category *string `json:"category"`
	Findings *string `json:"findings"`
	Draft    *string `json:"draft"`
	Review   *Review `json:"review"`
}

// Handoff is one handoff: the worker and task as the model sent them, and the state diff it caused.
type Handoff struct {
	Worker  any            `json:"worker"`
	Task    any            `json:"task"`
	IsError bool           `json:"is_error"`
	Changed map[string]any `json:"changed"`
}

// SupervisorOutput is the supervisor's result.
type SupervisorOutput struct {
	Reply     string      `json:"reply"`
	Stop      string      `json:"stop"`
	ToolCalls []ToolCall  `json:"tool_calls"`
	Errors    []DeskError `json:"errors"`
	Handoffs  []Handoff   `json:"handoffs"`
	State     State       `json:"state"`
}

func (s *State) passed() bool { return s.Review != nil && s.Review.Passed }

// get returns a field as a plain value (string, Review or nil) so an update can be compared with it.
func (s *State) get(k string) any {
	var p *string
	switch k {
	case "category":
		p = s.Category
	case "findings":
		p = s.Findings
	case "draft":
		p = s.Draft
	case "review":
		if s.Review == nil {
			return nil
		}
		return *s.Review
	}
	if p == nil {
		return nil
	}
	return *p
}

func (s *State) set(k string, v any) {
	str := func() *string {
		if x, ok := v.(string); ok {
			return &x
		}
		return nil
	}
	switch k {
	case "category":
		s.Category = str()
	case "findings":
		s.Findings = str()
	case "draft":
		s.Draft = str()
	case "review":
		if r, ok := v.(Review); ok {
			s.Review = &r
		} else {
			s.Review = nil
		}
	}
}

// apply merges a worker's update into the state and logs it. Changed holds only the fields whose value
// changed (the compact diff); the trace record's name is "<worker>: <field>, <field>" (sorted), or
// "<worker>: no change", or "<worker>: error" when the handoff failed (update nil).
func apply(run *DeskRun, out *SupervisorOutput, worker string, task any, update map[string]any, ms int) {
	changed := map[string]any{}
	keys := make([]string, 0, len(update))
	for k, v := range update {
		if !reflect.DeepEqual(out.State.get(k), v) {
			changed[k] = v
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	for _, k := range keys {
		out.State.set(k, changed[k])
	}
	out.Handoffs = append(out.Handoffs, Handoff{worker, task, update == nil, changed})
	what := strings.Join(keys, ", ")
	if update == nil {
		what = "error"
	} else if what == "" {
		what = "no change"
	}
	run.add(TraceRecord{Agent: "supervisor", Kind: "handoff", Name: worker + ": " + what, Ms: intp(ms)}) // a known worker (R37)
}

// ── 4. The workers ──────────────────────────────────────────────────────────

// handoffError is a worker's failure the supervisor gets back as an error result (not a crash).
type handoffError string

func (e handoffError) Error() string { return string(e) }

type workerEnv struct {
	run   *DeskRun
	out   *SupervisorOutput
	m     Model
	t     Ticket
	tools Tools
	pr    Prompts
}

func strOr(p *string, dflt string) string {
	if p == nil {
		return dflt
	}
	return *p
}

func taskMsg(e workerEnv, task string) []Message {
	return ticketMsg(e.t, fmt.Sprintf("\n\n<category>%s</category>\n<findings>\n%s\n</findings>\n\nYour task: %s",
		strOr(e.out.State.Category, "unknown"), strOr(e.out.State.Findings, "none"), task))
}

// triage: no tools. The lab's triage prompt; the answer must be one of its categories and priorities.
// A new category clears the review (R30), as a new draft does: a pass is for the category the label is
// bound to (R27), so re-triage must not move the allowed label without a fresh review.
func triage(e workerEnv, task string) (string, map[string]any, error) {
	system := e.pr.Triage["v2_rules"] + "\n\nAnswer with JSON only: {\"category\": \"<category>\", \"priority\": \"<priority>\"}"
	r, err := deskAsk(e.run, e.m, "triage", workerRole["triage"], system, taskMsg(e, task), nil)
	if err != nil {
		return "", nil, err
	}
	got, err := firstJSON(text(r), "triage")
	if err != nil {
		return "", nil, handoffError(err.Error())
	}
	obj, _ := got.(map[string]any)
	category, _ := obj["category"].(string)
	priority, _ := obj["priority"].(string)
	if !slices.Contains(Categories, category) || !slices.Contains(Priorities, priority) {
		return "", nil, handoffError(fmt.Sprintf("triage: category must be one of %v and priority one of %v", Categories, Priorities))
	}
	update := map[string]any{"category": category}
	if e.out.State.Category == nil || *e.out.State.Category != category {
		update["review"] = nil
	}
	return pyJSON(map[string]any{"category": category, "priority": priority}), update, nil
}

// research: lumen-read only, a capped tool loop. A label_ticket it asks for is refused before the tools:
// the worker was never offered it, and the ops tool is the supervisor's alone.
func research(e workerEnv, task string) (string, map[string]any, error) {
	system := "You research a Lumen support ticket with the read tools. Report the facts you found in two or three sentences. " + Rules
	msgs, found := taskMsg(e, task), ""
	for step := range MaxWorkerSteps {
		r, err := deskAsk(e.run, e.m, "research", workerRole["research"], system, msgs, ReadTools)
		if err != nil {
			return "", nil, err
		}
		if s := text(r); s != "" {
			found = s
		}
		u := uses(r)
		if len(u) == 0 {
			break
		}
		results := []map[string]any{}
		for _, use := range u {
			res, err := deskTool(&e.out.ToolCalls, &e.out.Errors, e.tools, "research", use, ReadTools, "")
			if err != nil {
				return "", nil, err
			}
			results = append(results, res)
		}
		msgs = append(slices.Clone(msgs), Message{"assistant", blocksOf(r)}, Message{"user", results})
		if step == MaxWorkerSteps-1 {
			stop(e.run, "research", "max_worker_steps") // findings are the last text it gave
		}
	}
	return found, map[string]any{"findings": found}, nil
}

// drafter: no tools. A new draft clears the review: a pass is for the draft it read, never the next one.
func drafter(e workerEnv, task string) (string, map[string]any, error) {
	r, err := deskAsk(e.run, e.m, "drafter", workerRole["drafter"], "You write the reply to a Lumen support ticket. Two or three sentences. "+Rules, taskMsg(e, task), nil)
	if err != nil {
		return "", nil, err
	}
	draft := text(r)
	return draft, map[string]any{"draft": draft, "review": nil}, nil
}

// reviewer: no tools. The LLMs lab's judge prompt and three rubric ids, on the same Model as the desk (R21).
func reviewer(e workerEnv, task string) (string, map[string]any, error) {
	if e.out.State.Draft == nil {
		return "", nil, handoffError("reviewer: there is no draft to review")
	}
	msg := []Message{{Role: "user", Content: fmt.Sprintf("<ticket>\n%s\n</ticket>\n\n<reply>\n%s\n</reply>\n\n"+
		"Answer with JSON only: an object with the keys %s, each {\"pass\": true|false, \"reason\": \"...\"}.",
		e.t.Text, *e.out.State.Draft, strings.Join(RubricIDs, ", "))}}
	r, err := deskAsk(e.run, e.m, "reviewer", workerRole["reviewer"], e.pr.judgeSystem(), msg, nil)
	if err != nil {
		return "", nil, err
	}
	got, err := firstJSON(text(r), "reviewer")
	if err != nil {
		return "", nil, handoffError(err.Error())
	}
	obj, _ := got.(map[string]any)
	review := Review{Passed: true, Verdict: map[string]bool{}}
	reasons := map[string]string{}
	for _, id := range RubricIDs {
		entry, _ := obj[id].(map[string]any)
		pass, ok := entry["pass"].(bool)
		if !ok {
			return "", nil, handoffError(fmt.Sprintf("reviewer: the reply must hold a boolean pass for each of %v", RubricIDs))
		}
		review.Verdict[id], review.Passed = pass, review.Passed && pass
		reasons[id] = reason(entry)
	}
	return pyJSON(map[string]any{"passed": review.Passed, "verdict": review.Verdict, "reasons": reasons}),
		map[string]any{"review": review}, nil
}

// reason is a rubric reason as text, the same in Python: a string as is, null or missing as "", anything
// else as JSON (Python writes a float such as 1.0 as "1.0", Go as "1": not pinned).
func reason(entry map[string]any) string {
	switch v := entry["reason"].(type) {
	case string:
		return v
	case nil:
		return ""
	default:
		return pyJSON(v)
	}
}

var runWorker = map[string]func(workerEnv, string) (string, map[string]any, error){
	"triage": triage, "research": research, "drafter": drafter, "reviewer": reviewer,
}

// ── 5. The supervisor ───────────────────────────────────────────────────────

// Supervisor is a supervisor agent that hands work to four workers through handoff(worker, task) tool calls.
//
// Why: each worker has one job and only the tools that job needs (research: lumen-read; the rest: none).
// The shared state is explicit, and every change to it is logged. The write is the supervisor's alone,
// and code, not the prompt, decides when: label_ticket is not in the supervisor's tools until the
// reviewer has passed the current draft, and a label_ticket that was not offered in that request is
// refused (so one asked for next to the reviewer's handoff fails), and its arguments must be this ticket
// and the triaged category (R27).
// Not when: the ticket is simple enough for one agent. The supervisor spends a model call per handoff on
// top of the workers' own, several times the single agent's tokens (lesson 6).
// The reply is the reviewed draft when the review passed, else the supervisor's last text (Stopped at the
// cap). Every cap leaves a kind "stop" trace record (R29).
func Supervisor(t Ticket, m Model, tools DeskTools, pr Prompts) (DeskRun, error) {
	run := DeskRun{}
	run.sink, _ = m.(TraceSink)
	if tools == nil {
		return run, fmt.Errorf("supervisor: no tools")
	}
	tools.MarkUntrusted() // the ticket text is in the first message (file comment)
	out := SupervisorOutput{Reply: Stopped, Stop: "max_turns", ToolCalls: []ToolCall{}, Errors: []DeskError{}, Handoffs: []Handoff{}, State: State{TicketID: t.ID}}
	env := workerEnv{&run, &out, m, t, tools, pr}
	msgs := ticketMsg(t, "")
	final := Stopped
	for turn := range MaxTurns {
		offered := []string{"handoff"} // the gate: rebuilt every turn
		if out.State.passed() {
			offered = append(offered, OpsTools...)
		}
		r, err := deskAsk(&run, m, "supervisor", "supervise", supervisorSystem, msgs, offered)
		if err != nil {
			return run, err
		}
		u := uses(r)
		if len(u) == 0 {
			final, out.Stop = text(r), "end_turn"
			break
		}
		results := []map[string]any{}
		for _, use := range u { // in the order asked, one at a time
			var res map[string]any
			if use.Name == "handoff" {
				res, err = handoff(env, use)
			} else { // an ops tool also needs the review to still be passed (a handoff earlier in this response may have reset it)
				var allowed []string
				if out.State.passed() {
					for _, name := range OpsTools {
						if slices.Contains(offered, name) {
							allowed = append(allowed, name)
						}
					}
				}
				res, err = deskTool(&out.ToolCalls, &out.Errors, tools, "supervisor", use, allowed, unbound(use, out.State))
			}
			if err != nil {
				return run, err
			}
			results = append(results, res)
		}
		msgs = append(slices.Clone(msgs), Message{"assistant", blocksOf(r)}, Message{"user", results})
		if turn == MaxTurns-1 {
			stop(&run, "supervisor", "max_turns")
		}
	}
	out.Reply = final
	if out.State.passed() {
		out.Reply = *out.State.Draft
	}
	run.Output = out
	return run, nil
}

// unbound is why a label_ticket may not run (R27): it may label only this ticket, with the category
// triage found. An injected target (t061's "label t001 outage") is refused here, before the guard or the
// server is asked. "" means it may run.
func unbound(use Block, s State) string {
	if use.Name != "label_ticket" {
		return ""
	}
	id, idOK := use.Input["id"].(string)
	label, labelOK := use.Input["label"].(string)
	if idOK && labelOK && id == s.TicketID && s.Category != nil && label == *s.Category {
		return ""
	}
	return fmt.Sprintf("label_ticket may only label this ticket (%s) with its triaged category (%s); nothing was written.",
		s.TicketID, strOr(s.Category, "None"))
}

// handoff runs one worker for a handoff tool call and returns its tool_result. A bad handoff (an unknown
// worker, a task that is not a string) or a worker failure is an error result for the supervisor to
// read, as the harness does with a failed tool: the state is unchanged (R26). An unknown worker is no
// handoff at all: it goes to Errors, never Handoffs or the trace (R37). A Model or Tools error is fatal.
func handoff(e workerEnv, use Block) (map[string]any, error) {
	worker, task := use.Input["worker"], use.Input["task"]
	name, ok := worker.(string)
	fn := runWorker[name]
	if !ok || fn == nil {
		who := name
		if !ok {
			who = pyJSON(worker) // the model may send any JSON here
		}
		out := fmt.Sprintf("Unknown worker: %s. Workers: %s", who, strings.Join(Workers, ", "))
		args := use.Input
		if args == nil {
			args = map[string]any{}
		}
		e.out.Errors = append(e.out.Errors, DeskError{"supervisor", "handoff", args, out})
		return map[string]any{"type": "tool_result", "tool_use_id": use.ID, "content": out, "is_error": true}, nil
	}
	start := time.Now()
	var out string
	var update map[string]any
	var err error
	taskStr, taskOK := task.(string)
	switch {
	case !taskOK:
		err = handoffError("handoff: task must be a string")
	default:
		out, update, err = fn(e, taskStr)
	}
	var he handoffError
	if errors.As(err, &he) {
		out, update = he.Error(), nil
	} else if err != nil {
		return nil, err
	}
	apply(e.run, e.out, name, task, update, int(time.Since(start).Milliseconds()))
	return map[string]any{"type": "tool_result", "tool_use_id": use.ID, "content": out, "is_error": update == nil}, nil
}

// ── 6. The engine ───────────────────────────────────────────────────────────
// Go keeps no registry: Python's desk_core.ENGINES names the engines, and Task 8 wires a Go engine to
// SingleAgent and Supervisor with the real Model (net/http to the proxy) and the MCP Tools.
