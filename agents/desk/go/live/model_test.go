package live

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	patterns "lumen-agents"
)

// fakeProxy answers each POST /v1/messages with the next scripted reply and keeps what it was sent.
type fakeProxy struct {
	mu      sync.Mutex
	bodies  []map[string]any
	headers []http.Header
	replies []string
	status  int
}

func (f *fakeProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	raw, _ := io.ReadAll(r.Body)
	var body map[string]any
	_ = json.Unmarshal(raw, &body)
	f.bodies = append(f.bodies, body)
	f.headers = append(f.headers, r.Header.Clone())
	if r.URL.Path != "/v1/messages" || r.Method != http.MethodPost {
		w.WriteHeader(400)
		return
	}
	w.Header().Set("content-type", "application/json")
	if f.status != 0 {
		w.WriteHeader(f.status)
		_, _ = io.WriteString(w, `{"type":"error","error":{"type":"replay_miss","message":"no recording for abcd1234; record with: python agents/run.py --engine hand-go --desk single --tickets t001 --mode record"}}`)
		return
	}
	n := len(f.bodies) - 1
	_, _ = io.WriteString(w, f.replies[n%len(f.replies)])
}

const textReply = `{"id":"msg_1","type":"message","role":"assistant","model":"claude-haiku-5-5","content":[{"type":"text","text":"  Hello.\n"}],"stop_reason":"end_turn","usage":{"input_tokens":12,"output_tokens":3}}`
const toolReply = `{"id":"msg_2","type":"message","role":"assistant","model":"claude-haiku-5-5","content":[{"type":"thinking","thinking":"x"},{"type":"tool_use","id":"toolu_1","name":"get_ticket","input":{"id":"t001"}}],"stop_reason":"tool_use","usage":{"input_tokens":20,"output_tokens":7}}`

func TestModelPostsTheMessagesRequest(t *testing.T) {
	fp := &fakeProxy{replies: []string{toolReply}}
	srv := httptest.NewServer(fp)
	defer srv.Close()
	log := &TraceLog{}
	schemas := map[string]map[string]any{"get_ticket": {"name": "get_ticket", "description": "d", "input_schema": map[string]any{"type": "object"}}}
	m := NewModel(srv.URL, schemas, log)
	msgs := []patterns.Message{
		{Role: "user", Content: "ticket"},
		{Role: "assistant", Content: []patterns.Block{{Type: "text", Text: "x"}, {Type: "tool_use", ID: "toolu_0", Name: "get_ticket"}}},
		{Role: "user", Content: []map[string]any{{"type": "tool_result", "tool_use_id": "toolu_0", "content": "r", "is_error": false}}},
	}
	reply, err := m.Call(patterns.Request{Agent: "single", System: "sys", Messages: msgs, Tools: []string{"get_ticket", "handoff"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(reply.Content) != 1 || reply.Content[0].Type != "tool_use" || reply.Content[0].Input["id"] != "t001" {
		t.Fatalf("reply %+v: want only the tool_use block", reply)
	}
	b := fp.bodies[0]
	if b["model"] != "claude-haiku-5-5" || b["max_tokens"] != float64(4000) || b["system"] != "sys" {
		t.Fatalf("body %v", b)
	}
	if _, ok := b["stream"]; ok {
		t.Fatal("streaming must be off")
	}
	tools := b["tools"].([]any)
	if len(tools) != 2 || tools[0].(map[string]any)["name"] != "get_ticket" || tools[1].(map[string]any)["name"] != "handoff" {
		t.Fatalf("tools %v: want the get_ticket schema and the handoff schema, in the order asked", tools)
	}
	asst := b["messages"].([]any)[1].(map[string]any)["content"].([]any)
	use := asst[1].(map[string]any)
	if in, ok := use["input"].(map[string]any); !ok || len(in) != 0 {
		t.Fatalf("a tool_use with no input must send input {}, got %v", use)
	}
	if _, ok := asst[0].(map[string]any)["id"]; ok {
		t.Fatalf("a text block carries only type and text, got %v", asst[0])
	}
	h := fp.headers[0]
	if h.Get("x-api-key") != "sk-ant-dummy" || h.Get("anthropic-version") == "" || !strings.HasPrefix(h.Get("content-type"), "application/json") {
		t.Fatalf("headers %v", h)
	}
	recs := log.Records()
	if len(recs) != 1 || recs[0].Kind != "model" || recs[0].Agent != "single" || *recs[0].TokensIn != 20 || *recs[0].TokensOut != 7 || recs[0].Ms == nil {
		t.Fatalf("trace %+v", recs)
	}
}

func TestModelNoToolsSendsNoToolsKey(t *testing.T) {
	fp := &fakeProxy{replies: []string{textReply}}
	srv := httptest.NewServer(fp)
	defer srv.Close()
	m := NewModel(srv.URL, nil, &TraceLog{})
	r, err := m.Call(patterns.Request{Agent: "writer", System: "s", Messages: []patterns.Message{{Role: "user", Content: "hi"}}})
	if err != nil || r.Content[0].Text != "  Hello.\n" {
		t.Fatalf("reply %+v err %v", r, err)
	}
	if _, ok := fp.bodies[0]["tools"]; ok {
		t.Fatal("no tools asked: the body must have no tools key (as desk_core.claude)")
	}
}

func TestModelUnknownToolIsAnError(t *testing.T) {
	m := NewModel("http://127.0.0.1:1", nil, &TraceLog{})
	if _, err := m.Call(patterns.Request{Agent: "a", Tools: []string{"write_file"}}); err == nil || !strings.Contains(err.Error(), "write_file") {
		t.Fatalf("err %v: a tool with no schema must fail before any request", err)
	}
}

func TestModelReplayMissNamesTheCommandAndNeverRetries(t *testing.T) {
	fp := &fakeProxy{status: 404}
	srv := httptest.NewServer(fp)
	defer srv.Close()
	_, err := NewModel(srv.URL, nil, &TraceLog{}).Call(patterns.Request{Agent: "a", Messages: []patterns.Message{{Role: "user", Content: "x"}}})
	var pe *ProxyError
	if err == nil || !asProxyError(err, &pe) || pe.Status != 404 {
		t.Fatalf("err %v: want a ProxyError 404", err)
	}
	if !strings.Contains(err.Error(), "replay miss") || !strings.Contains(err.Error(), "record with: python agents/run.py") {
		t.Fatalf("err %q must say replay miss and carry the record command", err)
	}
	if len(fp.bodies) != 1 {
		t.Fatalf("%d requests: a refusal is never retried", len(fp.bodies))
	}
}

func TestTraceLogSinksDeskRecords(t *testing.T) {
	var _ patterns.TraceSink = &Model{} // the desks hand their handoff and stop records to the Model's log
	log := &TraceLog{}
	m := NewModel("http://127.0.0.1:1", nil, log)
	m.Record(patterns.TraceRecord{Agent: "supervisor", Kind: "handoff", Name: "triage: category"})
	if len(log.Records()) != 1 {
		t.Fatal("Record must append to the shared log")
	}
}
