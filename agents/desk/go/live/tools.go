package live

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	patterns "lumen-agents"
)

// ── 3. The Tools: lumen-read and lumen-ops over MCP stdio ───────────────────
// Go mirrors desk_core.open_desk_tools and mcp/python/agent.py (import there, re-stated here):
//   - the tools are exactly the servers' tools (lumen-read: classify_complaint, get_ticket, search_tickets;
//     lumen-ops: label_ticket), checked when the session opens;
//   - a tool without readOnlyHint needs approval (the harness gate). The approver allows it, unless the session
//     has read untrusted text (needs_human): then the scripted person (`approve`) decides;
//   - a result taints the session when it is a ticket tool's, has no structured content, or carries an
//     untrusted_body anywhere (taints);
//   - lumen-ops' own confirmation round is answered with `elicit`;
//   - one call at a time (the per-session lock), in the order the desk asks.
// lumen-read runs as mcp/python/read_server.py (stdio is its default). lumen-ops is HTTP-only for other hosts,
// so the engine spawns agents/desk/python/ops_stdio.py: the same build_ops_server(), the pipe private to us.

var untrustedTools = []string{"search_tickets", "get_ticket"} // their results are ticket text, even when empty

// ToolsConfig is one session's settings.
type ToolsConfig struct {
	Python  string   // the interpreter that runs the servers (.venv-agents)
	Root    string   // the repo root
	Servers []string // "read" and/or "ops"
	Approve string   // the scripted person the guard asks: "accept" or "decline"
	Elicit  string   // the scripted answer to lumen-ops' confirmation: "accept" or "decline"
	Labels  string   // lumen-ops' label file
	Agent   string   // the Trace agent name of this session's tool records
	Log     *TraceLog
}

type tool struct {
	server        string
	needsApproval bool
}

// Tools is the real patterns.DeskTools: one MCP session per server, the gate, the guard and the lock.
type Tools struct {
	cfg       ToolsConfig
	sessions  map[string]*mcp.ClientSession
	tools     map[string]tool
	schemas   map[string]map[string]any
	untrusted atomic.Bool
	mu        sync.Mutex
}

func (cfg ToolsConfig) command(server string) *exec.Cmd {
	if server == "read" {
		return exec.Command(cfg.Python, filepath.Join(cfg.Root, "mcp", "python", "read_server.py"))
	}
	return exec.Command(cfg.Python, filepath.Join(cfg.Root, "agents", "desk", "python", "ops_stdio.py"), "--labels", cfg.Labels)
}

// elicitation answers lumen-ops' confirmation with the scripted answer: on accept, every boolean field the form
// asks for is true (as mcp/go/client does); anything else is declined rather than guessed.
func elicitation(answer string) func(context.Context, *mcp.ElicitRequest) (*mcp.ElicitResult, error) {
	return func(_ context.Context, req *mcp.ElicitRequest) (*mcp.ElicitResult, error) {
		if answer != "accept" {
			return &mcp.ElicitResult{Action: "decline"}, nil
		}
		schema, _ := generic(req.Params.RequestedSchema).(map[string]any)
		props, _ := schema["properties"].(map[string]any)
		content := map[string]any{}
		for name, p := range props {
			if m, _ := p.(map[string]any); m["type"] != "boolean" {
				return &mcp.ElicitResult{Action: "decline"}, nil
			}
			content[name] = true
		}
		return &mcp.ElicitResult{Action: "accept", Content: content}, nil
	}
}

// OpenTools starts the servers and lists their tools. Close stops them.
func OpenTools(cfg ToolsConfig) (*Tools, error) {
	t := &Tools{cfg: cfg, sessions: map[string]*mcp.ClientSession{}, tools: map[string]tool{}, schemas: map[string]map[string]any{}}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	want := []string{}
	for _, server := range cfg.Servers {
		if server != "read" && server != "ops" {
			return nil, fmt.Errorf("unknown server %q", server)
		}
		opts := &mcp.ClientOptions{}
		if server == "ops" {
			opts.ElicitationHandler = elicitation(cfg.Elicit) // declares the capability: the confirmation runs
			want = append(want, patterns.OpsTools...)
		} else {
			want = append(want, patterns.ReadTools...)
		}
		cmd := cfg.command(server)
		cmd.Stderr = os.Stderr
		client := mcp.NewClient(&mcp.Implementation{Name: "lumen-desk-go", Version: "0.1.0"}, opts)
		session, err := client.Connect(ctx, &mcp.CommandTransport{Command: cmd}, nil)
		if err != nil {
			_ = t.Close()
			return nil, fmt.Errorf("connect to lumen-%s: %w", server, err)
		}
		t.sessions[server] = session
		list, err := session.ListTools(ctx, nil)
		if err != nil {
			_ = t.Close()
			return nil, fmt.Errorf("list lumen-%s tools: %w", server, err)
		}
		for _, mt := range list.Tools {
			readOnly := mt.Annotations != nil && mt.Annotations.ReadOnlyHint
			t.tools[mt.Name] = tool{server: server, needsApproval: !readOnly}
			t.schemas[mt.Name] = map[string]any{"name": mt.Name, "description": mt.Description, "input_schema": generic(mt.InputSchema)}
		}
	}
	got := make([]string, 0, len(t.tools))
	for n := range t.tools {
		got = append(got, n)
	}
	slices.Sort(got)
	slices.Sort(want)
	if !slices.Equal(got, want) {
		_ = t.Close()
		return nil, fmt.Errorf("desk tools must be exactly %v, got %v", want, got)
	}
	return t, nil
}

// Schemas is every tool's {name, description, input_schema}, for the Model.
func (t *Tools) Schemas() map[string]map[string]any { return t.schemas }

// MarkUntrusted sets the untrusted-read flag (R24: the desks call it before the first model call).
func (t *Tools) MarkUntrusted() { t.untrusted.Store(true) }

// Untrusted reports the flag.
func (t *Tools) Untrusted() bool { return t.untrusted.Load() }

// approve is mcp/python/agent.py's make_approver with guard on: a write after untrusted text needs a yes.
func (t *Tools) approve(name string, args map[string]any) bool {
	if !t.untrusted.Load() { // needs_human(tool, read_untrusted, guard=True)
		return true
	}
	allowed := t.cfg.Approve == "accept"
	answer := map[bool]string{true: "yes", false: "no"}[allowed]
	fmt.Fprintf(os.Stderr, "  ! guard: untrusted ticket text was read this turn; asking the person about %s(%s) -> %s (scripted)\n",
		name, firstN(patterns.PyJSON(args), 120), answer)
	return allowed
}

// Run runs one tool call (desk_core's run_tool): (text, isError). err is reserved for the engine's own
// failures; a tool's failure, a refusal or a server error is text for the model.
func (t *Tools) Run(name string, args map[string]any) (string, bool, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	start := time.Now()
	out, isErr := t.run(name, args)
	t.cfg.Log.Record(patterns.TraceRecord{Agent: t.cfg.Agent, Kind: "tool", Name: name, Ms: intp(int(time.Since(start).Milliseconds()))})
	return out, isErr, nil
}

func (t *Tools) run(name string, args map[string]any) (string, bool) {
	tl, ok := t.tools[name]
	if !ok {
		return "Unknown tool: " + name, true
	}
	if tl.needsApproval && !t.approve(name, args) {
		return "The user denied this action.", true
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	res, err := t.sessions[tl.server].CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: args})
	if err != nil { // a protocol error: information for the model, as Python's run_tool reports an exception
		return "McpError: " + err.Error(), true
	}
	structured := generic(res.StructuredContent)
	if taints(name, structured) {
		t.untrusted.Store(true)
	}
	if res.IsError {
		return "ToolError: " + resultText(res, structured), true
	}
	return resultText(res, structured), false
}

// taints is mcp/python/agent.py's rule: fails closed.
func taints(name string, structured any) bool {
	return slices.Contains(untrustedTools, name) || structured == nil || carriesUntrusted(structured)
}

func carriesUntrusted(v any) bool {
	switch x := v.(type) {
	case map[string]any:
		if _, ok := x["untrusted_body"]; ok {
			return true
		}
		for _, e := range x {
			if carriesUntrusted(e) {
				return true
			}
		}
	case []any:
		for _, e := range x {
			if carriesUntrusted(e) {
				return true
			}
		}
	}
	return false
}

// resultText is agent.py's result_text: structured content as Python-style JSON, else the text blocks.
func resultText(res *mcp.CallToolResult, structured any) string {
	if structured != nil && !res.IsError {
		return patterns.PyJSON(structured)
	}
	var parts []string
	for _, c := range res.Content {
		if tc, ok := c.(*mcp.TextContent); ok {
			parts = append(parts, tc.Text)
		}
	}
	if len(parts) == 0 {
		return "<no text>"
	}
	return strings.Join(parts, "\n")
}

// Close ends every session (and so its server process).
func (t *Tools) Close() error {
	var first error
	for _, s := range t.sessions {
		if err := s.Close(); err != nil && first == nil {
			first = err
		}
	}
	t.sessions = map[string]*mcp.ClientSession{}
	return first
}

// generic is v as plain JSON values (map[string]any, []any, float64, ...), whatever SDK type holds it; nil stays nil.
func generic(v any) any {
	if v == nil {
		return nil
	}
	raw, err := json.Marshal(v)
	if err != nil || string(raw) == "null" {
		return nil
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil
	}
	return out
}
