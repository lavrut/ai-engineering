package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
)

// callModel against a fake Messages API on 127.0.0.1: a dummy key, no network, nothing paid.

const okReply = `{"id":"msg_1","type":"message","role":"assistant","model":"claude-opus-5-5",` +
	`"content":[{"type":"text","text":"hello"}],"stop_reason":"end_turn","stop_sequence":null,` +
	`"usage":{"input_tokens":12,"output_tokens":3}}`
const badReply = `{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens: too large"}}`

type seenRequest struct {
	path   string
	header http.Header
	body   map[string]any
}

// upstream starts a local server that records each request and answers with status and body.
func upstream(t *testing.T, status int, body string) *[]seenRequest {
	t.Helper()
	seen := &[]seenRequest{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var b map[string]any
		json.Unmarshal(raw, &b)
		*seen = append(*seen, seenRequest{r.URL.Path, r.Header.Clone(), b})
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		io.WriteString(w, body)
	}))
	t.Cleanup(srv.Close)
	if !strings.HasPrefix(srv.URL, "http://127.0.0.1:") {
		t.Fatalf("fake upstream must bind 127.0.0.1, got %s", srv.URL)
	}
	old := client
	client = anthropic.NewClient(option.WithBaseURL(srv.URL), option.WithAPIKey("test-key"), option.WithMaxRetries(0))
	t.Cleanup(func() { client = old })
	return seen
}

func events(t *testing.T) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(traceFile)
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var e map[string]any
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			t.Fatal(err)
		}
		out = append(out, e)
	}
	return out
}

func userHi() []anthropic.MessageParam {
	return []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock("hi"))}
}

func TestCallModelRequestShapeAndReply(t *testing.T) {
	setup(t)
	seen := upstream(t, 200, okReply)
	resp, err := callModel(context.Background(), anthropic.MessageNewParams{Messages: userHi()})
	if err != nil || textOf(resp) != "hello" || resp.StopReason != "end_turn" {
		t.Fatalf("resp %+v, err %v", resp, err)
	}
	req := (*seen)[0]
	if req.path != "/v1/messages" || req.header.Get("anthropic-beta") != "server-side-fallback-2026-07-01" ||
		req.header.Get("X-Api-Key") != "test-key" {
		t.Errorf("path %s, headers %v", req.path, req.header)
	}
	b := req.body
	if b["model"] != "claude-opus-5-5" || b["max_tokens"] != float64(16000) || b["fallbacks"] != "default" {
		t.Errorf("body %v", b)
	}
	system, _ := json.Marshal(b["system"])
	if !strings.Contains(string(system), `"text":"You are a careful coding assistant working inside `) {
		t.Errorf("system %s", system)
	}
	var names []string
	for _, tool := range b["tools"].([]any) {
		names = append(names, tool.(map[string]any)["name"].(string))
	}
	if strings.Join(names, ",") != "list_files,read_file,write_file" {
		t.Errorf("tools %v", names)
	}
	ev := events(t)
	if len(ev) != 2 || ev[0]["event"] != "model_request" || ev[1]["event"] != "model_response" ||
		ev[1]["input_tokens"] != float64(12) || ev[1]["output_tokens"] != float64(3) {
		t.Errorf("trace %v", ev)
	}
}

func TestCallModelKeepsACallersMaxTokens(t *testing.T) {
	setup(t)
	seen := upstream(t, 200, okReply)
	_, err := callModel(context.Background(), anthropic.MessageNewParams{Messages: userHi(), MaxTokens: 4000,
		ToolChoice: anthropic.ToolChoiceUnionParam{OfNone: &anthropic.ToolChoiceNoneParam{}}})
	if err != nil {
		t.Fatal(err)
	}
	b := (*seen)[0].body
	choice, _ := b["tool_choice"].(map[string]any)
	if b["max_tokens"] != float64(4000) || choice["type"] != "none" {
		t.Errorf("body %v", b)
	}
}

func TestCallModelErrorStatus(t *testing.T) {
	setup(t)
	upstream(t, 400, badReply)
	_, err := callModel(context.Background(), anthropic.MessageNewParams{Messages: userHi()})
	var apiErr *anthropic.Error
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 400 {
		t.Fatalf("err %v", err)
	}
	if ev := events(t); len(ev) != 1 || ev[0]["event"] != "model_request" {
		t.Errorf("trace %v", ev)
	}
}
