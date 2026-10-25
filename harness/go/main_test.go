package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/anthropics/anthropic-sdk-go"
)

func setup(t *testing.T) {
	t.Helper()
	workspace = t.TempDir()
	traceFile = filepath.Join(t.TempDir(), "trace.jsonl")
}

// reply builds a fake model response from literal JSON content blocks.
func reply(t *testing.T, stop, content string, inputTokens int) *anthropic.Message {
	t.Helper()
	raw := `{"id":"m","type":"message","role":"assistant","model":"claude-opus-5-5","content":` + content +
		`,"stop_reason":"` + stop + `","usage":{"input_tokens":` + itoa(inputTokens) + `,"output_tokens":10}}`
	var m anthropic.Message
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatal(err)
	}
	return &m
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

const listCall = `[{"type":"tool_use","id":"t1","name":"list_files","input":{"path":"."}}]`

func textBlock(s string) string {
	b, _ := json.Marshal(s)
	return `[{"type":"text","text":` + string(b) + `}]`
}

type result struct {
	ToolUseID string `json:"tool_use_id"`
	IsError   bool   `json:"is_error"`
	Content   []struct {
		Text string `json:"text"`
	} `json:"content"`
}

func (r result) text() string {
	if len(r.Content) == 0 {
		return ""
	}
	return r.Content[0].Text
}

func decode(t *testing.T, blocks []anthropic.ContentBlockParamUnion) []result {
	t.Helper()
	out := make([]result, len(blocks))
	for i, b := range blocks {
		raw, err := json.Marshal(b)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(raw, &out[i]); err != nil {
			t.Fatal(err)
		}
	}
	return out
}

func yes(string, string) bool { return true }
func no(string, string) bool  { return false }

func TestSafePathRejectsEscapes(t *testing.T) {
	setup(t)
	for _, bad := range []string{"../x", "/etc/passwd", "a/../../x"} {
		if _, err := safePath(bad); err == nil {
			t.Errorf("safePath(%q) should fail", bad)
		}
	}
}

func TestExecutorOrderAndErrors(t *testing.T) {
	setup(t)
	os.WriteFile(filepath.Join(workspace, "a.txt"), []byte("A"), 0o644)
	out := decode(t, executeTools(context.Background(), []call{
		{"t1", "read_file", json.RawMessage(`{"path":"a.txt"}`)},
		{"t2", "list_files", json.RawMessage(`{"path":"."}`)},
	}, yes))
	if out[0].ToolUseID != "t1" || out[1].ToolUseID != "t2" {
		t.Fatalf("order/ids wrong: %+v", out)
	}
	if out[0].text() != "A" || out[0].IsError {
		t.Fatalf("read_file result wrong: %+v", out[0])
	}

	out = decode(t, executeTools(context.Background(), []call{
		{"t1", "write_file", json.RawMessage(`{"path":"x","content":"y"}`)},
		{"t2", "nope", json.RawMessage(`{}`)},
		{"t3", "read_file", json.RawMessage(`{`)},
		{"t4", "read_file", json.RawMessage(`{"path":"../../etc/passwd"}`)},
	}, no))
	for i, r := range out {
		if !r.IsError {
			t.Errorf("result %d should be an error: %+v", i, r)
		}
	}
	if out[0].text() != "The user denied this action." {
		t.Errorf("denied: %q", out[0].text())
	}
	if out[1].text() != "Unknown tool: nope" {
		t.Errorf("unknown: %q", out[1].text())
	}
	if _, err := os.Stat(filepath.Join(workspace, "x")); err == nil {
		t.Error("denied write must not create a file")
	}
}

func TestLoopToolsThenText(t *testing.T) {
	setup(t)
	replies := []*anthropic.Message{reply(t, "tool_use", listCall, 100), reply(t, "end_turn", textBlock("done"), 100)}
	var seen [][]string
	fake := func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error) {
		var roles []string
		for _, m := range p.Messages {
			roles = append(roles, string(m.Role))
		}
		seen = append(seen, roles)
		r := replies[0]
		replies = replies[1:]
		return r, nil
	}
	got, err := runAgent(context.Background(), "hi", fake, yes)
	if err != nil || got != "done" {
		t.Fatalf("got %q, %v", got, err)
	}
	if strings.Join(seen[1], ",") != "user,assistant,user" {
		t.Fatalf("second call roles: %v", seen[1])
	}
}

func TestStopReasons(t *testing.T) {
	setup(t)
	run := func(stop, content string) string {
		fake := func(context.Context, anthropic.MessageNewParams) (*anthropic.Message, error) {
			return reply(t, stop, content, 100), nil
		}
		got, err := runAgent(context.Background(), "hi", fake, yes)
		if err != nil {
			t.Fatal(err)
		}
		return got
	}
	if got := run("max_tokens", textBlock("part")); got != "part\n[stopped: the response hit max_tokens]" {
		t.Errorf("max_tokens: %q", got)
	}
	if got := run("refusal", `[]`); got != "[the model declined this request]" {
		t.Errorf("refusal: %q", got)
	}

	old := maxTurns
	maxTurns = 2
	defer func() { maxTurns = old }()
	calls := 0
	fake := func(context.Context, anthropic.MessageNewParams) (*anthropic.Message, error) {
		calls++
		return reply(t, "tool_use", listCall, 100), nil
	}
	got, _ := runAgent(context.Background(), "hi", fake, yes)
	if got != "[stopped: reached maxTurns without finishing]" || calls != 2 {
		t.Errorf("maxTurns: %q after %d calls", got, calls)
	}
}

func TestCompactionStartsFresh(t *testing.T) {
	setup(t)
	replies := []*anthropic.Message{
		reply(t, "tool_use", listCall, contextBudget+1),
		reply(t, "end_turn", textBlock("the summary"), 100),
		reply(t, "end_turn", textBlock("done"), 100),
	}
	var calls []anthropic.MessageNewParams
	fake := func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error) {
		calls = append(calls, p)
		r := replies[0]
		replies = replies[1:]
		return r, nil
	}
	got, err := runAgent(context.Background(), "hi", fake, yes)
	if err != nil || got != "done" {
		t.Fatalf("got %q, %v", got, err)
	}
	if calls[1].ToolChoice.OfNone == nil || calls[1].MaxTokens != 4000 {
		t.Errorf("summary call params wrong: %+v", calls[1])
	}
	third := calls[2].Messages
	if len(third) != 1 || third[0].Role != anthropic.MessageParamRoleUser {
		t.Fatalf("third call should be one user message, got %d", len(third))
	}
	raw, _ := json.Marshal(third[0])
	if !strings.Contains(string(raw), `"text":"Summary of earlier work:`) {
		t.Errorf("summary prefix missing: %s", raw)
	}
}
