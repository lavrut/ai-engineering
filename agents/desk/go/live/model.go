// Package live is the hand-go engine's real Model and Tools (ruling R23): a Model that POSTs each call to the
// record/replay proxy, and Tools that reach lumen-read and lumen-ops over MCP, behind the same permission gate
// and untrusted-read rule as desk_core.open_desk_tools in Python.
//
// The patterns and desks in package patterns (agents/desk/go) take any Model and Tools; the contract tests
// script them. This package is what agents/run.py's hand-go engine plugs in (cmd/handgo).
package live

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	patterns "lumen-agents"
)

// ── 1. Configuration ────────────────────────────────────────────────────────

const (
	ProxyURL         = "http://127.0.0.1:8790" // agents/proxy/proxy.py: HOST and DEFAULT_PORT
	DummyKey         = "sk-ant-dummy"          // the proxy injects the real key in record mode
	DefaultModel     = "claude-haiku-5-5"
	MaxTokens        = 4000 // per call; matches the proxy's MAX_TOKENS
	AnthropicVersion = "2023-06-01"
	callTimeout      = 10 * time.Minute // the proxy buffers a whole reply in record mode
)

// TraceLog is the run's trace: one ordered list of R2 records (model, tool, handoff, stop) that the Model, the
// Tools and the desks (through the Model's Record) all append to. Safe for concurrent use (parallel lookups).
type TraceLog struct {
	mu   sync.Mutex
	recs []patterns.TraceRecord
}

// Record appends one record.
func (l *TraceLog) Record(r patterns.TraceRecord) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.recs = append(l.recs, r)
}

// Records returns a copy of the log.
func (l *TraceLog) Records() []patterns.TraceRecord {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]patterns.TraceRecord{}, l.recs...)
}

func intp(n int) *int { return &n }

// ── 2. The Model: one Messages API call through the proxy ───────────────────

// ProxyError is the proxy refusing a call: 404 (a replay miss, whose message names the record command) or
// 402 (the cost cap). Never retried: retrying cannot help.
type ProxyError struct {
	Status  int
	Kind    string
	Message string
}

func (e *ProxyError) Error() string {
	what := map[int]string{404: "replay miss", 402: "cost cap"}[e.Status]
	if e.Status == 404 && e.Kind != "replay_miss" {
		what = "not found"
	}
	return fmt.Sprintf("proxy answered %d (%s): %s", e.Status, what, e.Message)
}

func asProxyError(err error, target **ProxyError) bool { return errors.As(err, target) }

// Model is the real patterns.Model: net/http to the proxy, streaming off, no retries (a retry would be another
// paid call in record mode), the dummy key. It resolves the tool names a Request offers to their schemas.
type Model struct {
	URL       string
	Name      string
	MaxTokens int
	Schemas   map[string]map[string]any // tool name -> {name, description, input_schema}
	Log       *TraceLog
	Client    *http.Client
}

// NewModel is a Model on url with the track's model and max_tokens. Schemas are the MCP tools' (Tools.Schemas);
// the handoff tool's schema (patterns.HandoffSchema) is always known.
func NewModel(url string, schemas map[string]map[string]any, log *TraceLog) *Model {
	all := map[string]map[string]any{"handoff": patterns.HandoffSchema}
	for k, v := range schemas {
		all[k] = v
	}
	return &Model{URL: url, Name: DefaultModel, MaxTokens: MaxTokens, Schemas: all, Log: log,
		Client: &http.Client{Timeout: callTimeout, CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse // the proxy never redirects; a redirect is followed nowhere
		}}}
}

// Record keeps a desk's handoff or stop record in the run's log (patterns.TraceSink).
func (m *Model) Record(r patterns.TraceRecord) { m.Log.Record(r) }

// wireBlocks is a reply's blocks as the Messages API takes them back: a text block is {type, text}, a tool_use
// block {type, id, name, input} with input {} when empty (Block's omitempty would drop it).
func wireBlocks(bs []patterns.Block) []map[string]any {
	out := make([]map[string]any, 0, len(bs))
	for _, b := range bs {
		if b.Type == "tool_use" {
			in := b.Input
			if in == nil {
				in = map[string]any{}
			}
			out = append(out, map[string]any{"type": "tool_use", "id": b.ID, "name": b.Name, "input": in})
		} else {
			out = append(out, map[string]any{"type": "text", "text": b.Text})
		}
	}
	return out
}

func wireMessages(msgs []patterns.Message) []map[string]any {
	out := make([]map[string]any, 0, len(msgs))
	for _, m := range msgs {
		var content any = m.Content
		if bs, ok := m.Content.([]patterns.Block); ok {
			content = wireBlocks(bs)
		}
		out = append(out, map[string]any{"role": m.Role, "content": content})
	}
	return out
}

type apiReply struct {
	Content []struct {
		Type  string         `json:"type"`
		Text  string         `json:"text"`
		ID    string         `json:"id"`
		Name  string         `json:"name"`
		Input map[string]any `json:"input"`
	} `json:"content"`
	Usage *struct {
		InputTokens  *int `json:"input_tokens"`
		OutputTokens *int `json:"output_tokens"`
	} `json:"usage"`
}

// Call sends one request and logs a "model" record (tokens from the reply's usage, ms of the round trip).
func (m *Model) Call(req patterns.Request) (patterns.Reply, error) {
	body := map[string]any{"model": m.Name, "max_tokens": m.MaxTokens, "system": req.System,
		"messages": wireMessages(req.Messages)}
	if len(req.Tools) > 0 {
		tools := make([]map[string]any, 0, len(req.Tools))
		for _, name := range req.Tools {
			s, ok := m.Schemas[name]
			if !ok {
				return patterns.Reply{}, fmt.Errorf("no schema for tool %q: it is no desk tool", name)
			}
			tools = append(tools, s)
		}
		body["tools"] = tools
	}
	raw, err := json.Marshal(body)
	if err != nil {
		return patterns.Reply{}, err
	}
	hreq, err := http.NewRequest(http.MethodPost, m.URL+"/v1/messages", bytes.NewReader(raw))
	if err != nil {
		return patterns.Reply{}, err
	}
	hreq.Header.Set("content-type", "application/json")
	hreq.Header.Set("x-api-key", DummyKey)
	hreq.Header.Set("anthropic-version", AnthropicVersion)
	start := time.Now()
	resp, err := m.Client.Do(hreq)
	if err != nil {
		return patterns.Reply{}, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return patterns.Reply{}, err
	}
	if resp.StatusCode != http.StatusOK {
		var e struct {
			Error struct {
				Type    string `json:"type"`
				Message string `json:"message"`
			} `json:"error"`
		}
		_ = json.Unmarshal(data, &e)
		if resp.StatusCode == 402 || resp.StatusCode == 404 {
			return patterns.Reply{}, &ProxyError{resp.StatusCode, e.Error.Type, e.Error.Message}
		}
		return patterns.Reply{}, fmt.Errorf("the API answered %d: %s", resp.StatusCode, firstN(string(data), 300))
	}
	var r apiReply
	if err := json.Unmarshal(data, &r); err != nil {
		return patterns.Reply{}, fmt.Errorf("reply is not a message: %w", err)
	}
	var reply patterns.Reply
	for _, b := range r.Content {
		switch b.Type { // text and tool_use only, as the Python patterns read a reply
		case "text":
			reply.Content = append(reply.Content, patterns.Block{Type: "text", Text: b.Text})
		case "tool_use":
			reply.Content = append(reply.Content, patterns.Block{Type: "tool_use", ID: b.ID, Name: b.Name, Input: b.Input})
		}
	}
	rec := patterns.TraceRecord{Agent: req.Agent, Kind: "model", Name: m.Name, Ms: intp(int(time.Since(start).Milliseconds()))}
	if r.Usage != nil {
		rec.TokensIn, rec.TokensOut = r.Usage.InputTokens, r.Usage.OutputTokens
	}
	m.Log.Record(rec)
	return reply, nil
}

func firstN(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}
