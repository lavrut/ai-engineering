package patterns

// The contract runner: agents/fixtures/contract.jsonl is replayed against a scripted model and a scripted
// tools stub, and each case's expect is compared as parsed JSON (the rules in agents/fixtures/README.md:
// floats within 1e-9, everything else exact). The tools stub returns fixed text: the contract never pins
// tool output text, only the call sequence, the scripted replies and each tool's is_error flag.

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

// ── 1. Scripted model and tools ─────────────────────────────────────────────
var errExhausted = errors.New("script exhausted")

type scriptedModel struct {
	mu        sync.Mutex
	script    []any
	reqs      []Request
	exhausted bool
}

func (s *scriptedModel) Call(req Request) (Reply, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := len(s.reqs)
	s.reqs = append(s.reqs, req)
	if n >= len(s.script) {
		s.exhausted = true
		return Reply{}, fmt.Errorf("call %d by %q: %w", n+1, req.Agent, errExhausted)
	}
	switch e := s.script[n].(type) {
	case string:
		return Reply{Content: []Block{{Type: "text", Text: e}}}, nil
	case map[string]any:
		use := e["tool_use"].(map[string]any)
		input, _ := use["input"].(map[string]any)
		return Reply{Content: []Block{{Type: "tool_use", ID: fmt.Sprintf("toolu_%03d", n), Name: use["name"].(string), Input: input}}}, nil
	}
	return Reply{}, fmt.Errorf("bad script entry %d", n)
}

// stubTools answers the read tools with fixed text and anything else as an unknown tool (an error). With ops
// (the desk cases: lumen-ops open, the person and the server both saying yes) label_ticket succeeds too.
type stubTools struct {
	mu    sync.Mutex
	names []string
	ops   bool
}

// MarkUntrusted is a no-op: the stub has no guard (the contract's person says yes anyway).
func (s *stubTools) MarkUntrusted() {}

func (s *stubTools) Run(name string, args map[string]any) (string, bool, error) {
	s.mu.Lock()
	s.names = append(s.names, name)
	s.mu.Unlock()
	switch name {
	case "get_ticket", "search_tickets", "classify_complaint":
		return "stub result for " + name, false, nil
	case "label_ticket":
		if s.ops {
			return "stub label written", false, nil
		}
	}
	return "Unknown tool: " + name, true, nil
}

// ── 2. Contract compare ─────────────────────────────────────────────────────
type contractCase struct {
	Name     string         `json:"name"`
	Pattern  string         `json:"pattern"`
	TicketID string         `json:"ticket_id"`
	Scripted []any          `json:"scripted"`
	Expect   map[string]any `json:"expect"`
}

func repoFile(parts ...string) string { return filepath.Join(append([]string{Root()}, parts...)...) }

func loadCases(t *testing.T) []contractCase {
	t.Helper()
	f, err := os.Open(repoFile("agents", "fixtures", "contract.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var out []contractCase
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		if strings.TrimSpace(sc.Text()) == "" {
			continue
		}
		var c contractCase
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			t.Fatal(err)
		}
		out = append(out, c)
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func loadTicket(t *testing.T, id string) Ticket {
	t.Helper()
	raw, err := os.ReadFile(repoFile("agents", "evals", "tickets.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var tk Ticket
		if err := json.Unmarshal([]byte(line), &tk); err != nil {
			t.Fatal(err)
		}
		if tk.ID == id {
			return tk
		}
	}
	t.Fatalf("no ticket %s", id)
	return Ticket{}
}

func loadPrompts(t *testing.T) Prompts {
	t.Helper()
	p, err := LoadPrompts(DefaultPromptsPath())
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// equal is deep equality on parsed JSON: floats within an absolute 1e-9, everything else exact.
func equal(a, b any) bool {
	switch x := a.(type) {
	case float64:
		y, ok := b.(float64)
		return ok && math.Abs(x-y) <= 1e-9
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !equal(x[i], y[i]) {
				return false
			}
		}
		return true
	case map[string]any:
		y, ok := b.(map[string]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for k, v := range x {
			w, present := y[k]
			if !present || !equal(v, w) {
				return false
			}
		}
		return true
	}
	return reflect.DeepEqual(a, b)
}

// generic round-trips any value through JSON so it compares as parsed JSON.
func generic(t *testing.T, v any) any {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// runCase runs one case and returns its output and model-call sequence (patterns: Run; desks: DeskRun).
func runCase(c contractCase, tk Ticket, m Model, pr Prompts) (output, calls any, err error) {
	switch c.Pattern {
	case "single", "supervisor":
		var run DeskRun
		if c.Pattern == "single" {
			run, err = SingleAgent(tk, m, &stubTools{ops: true})
		} else {
			run, err = Supervisor(tk, m, &stubTools{ops: true}, pr)
		}
		return run.Output, run.Calls, err
	}
	run, err := runPattern(c, tk, m, &stubTools{}, pr)
	return run.Output, run.Calls, err
}

func runPattern(c contractCase, tk Ticket, m Model, tools Tools, pr Prompts) (Run, error) {
	switch c.Pattern {
	case "chain":
		return Chain(tk, m, pr)
	case "route":
		return Route(tk, m)
	case "parallel":
		return Parallel(tk, m, tools, 1)
	case "orchestrate":
		return Orchestrate(tk, m, tools)
	case "evaluate_optimize":
		return EvaluateOptimize(tk, MaxRounds, m, pr)
	}
	return Run{}, fmt.Errorf("unknown pattern %q", c.Pattern)
}

func TestContract(t *testing.T) {
	cases := loadCases(t)
	if len(cases) < 48 {
		t.Fatalf("contract has %d cases, want at least 48", len(cases))
	}
	pr := loadPrompts(t)
	errorCases := 0
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			m := &scriptedModel{script: c.Scripted}
			output, calls, err := runCase(c, loadTicket(t, c.TicketID), m, pr)
			if m.exhausted {
				t.Fatalf("the pattern asked for more replies than scripted (%d): %v", len(c.Scripted), err)
			}
			if want, _ := c.Expect["error"].(bool); want {
				errorCases++
				if err == nil {
					t.Fatalf("expected an error, got output %+v", output)
				}
				if len(c.Expect) != 1 {
					t.Fatalf("an error case holds only expect.error, got %v", c.Expect)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if len(m.reqs) != len(c.Scripted) {
				t.Fatalf("%d scripted replies, only %d used", len(c.Scripted), len(m.reqs))
			}
			got := map[string]any{"output": generic(t, output), "calls": generic(t, calls)}
			if !equal(got, generic(t, c.Expect)) {
				g, _ := json.MarshalIndent(got, "", " ")
				w, _ := json.MarshalIndent(c.Expect, "", " ")
				t.Fatalf("mismatch\n got: %s\nwant: %s", g, w)
			}
		})
	}
	if errorCases < 10 {
		t.Fatalf("only %d error cases ran", errorCases)
	}
}

// ── 3. Focused checks beyond the contract ───────────────────────────────────
// byAgent answers by the caller's agent name, so goroutine arrival order does not matter.
type byAgent func(agent string) Reply

func (f byAgent) Call(req Request) (Reply, error) { return f(req.Agent), nil }

func TestParallelWithWorkersKeepsSlotOrder(t *testing.T) {
	use := func(name string, input map[string]any) Reply {
		return Reply{Content: []Block{{Type: "tool_use", ID: "u", Name: name, Input: input}}}
	}
	m := byAgent(func(agent string) Reply {
		switch agent {
		case "lookup-1":
			return use("get_ticket", map[string]any{"id": "t001"})
		case "lookup-2", "lookup-3":
			return use("search_tickets", map[string]any{"query": agent})
		}
		return Reply{Content: []Block{{Type: "text", Text: "done"}}}
	})
	tools := &stubTools{}
	run, err := Parallel(loadTicket(t, "t001"), m, tools, 3) // -race checks the fan-out
	if err != nil {
		t.Fatal(err)
	}
	if got := run.Calls[:3]; got[0].Agent != "lookup-1" || got[1].Agent != "lookup-2" || got[2].Agent != "lookup-3" {
		t.Fatalf("calls not in slot order: %v", got)
	}
	if want := []string{"get_ticket", "search_tickets", "search_tickets"}; !reflect.DeepEqual(tools.names, want) {
		t.Fatalf("tool order %v, want %v", tools.names, want)
	}
}

func TestOrchestrateWorkersGetReadToolsOnly(t *testing.T) {
	m := &scriptedModel{script: []any{`["x"]`, "done", "final"}}
	if _, err := Orchestrate(loadTicket(t, "t001"), m, &stubTools{}); err != nil {
		t.Fatal(err)
	}
	for _, name := range m.reqs[1].Tools {
		if name == "label_ticket" {
			t.Fatal("a worker was offered label_ticket")
		}
	}
	if len(m.reqs[1].Tools) != len(ReadTools) {
		t.Fatalf("worker tools %v", m.reqs[1].Tools)
	}
}

func TestASCIIOnlyNormalisation(t *testing.T) {
	if got := asciiLower("İAbC"); got != "İabc" {
		t.Fatalf("asciiLower folded a non-ASCII letter: %q", got)
	}
	r := Reply{Content: []Block{{Type: "text", Text: "  x  \t\r\n"}}}
	if got := text(r); got != "  x  " {
		t.Fatalf("text trimmed more than ASCII whitespace: %q", got)
	}
}

func TestFirstJSONParsesFromFirstBrace(t *testing.T) {
	v, err := firstJSON("Sure! [1] is not it, but {\"a\": 1} is", "t")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := v.([]any); !ok {
		t.Fatalf("the first bracket comes first, got %T", v)
	}
	if _, err := firstJSON("no json here", "t"); err == nil {
		t.Fatal("expected an error")
	}
}
