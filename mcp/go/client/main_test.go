package main

// The tests drive the client's functions against an in-process test server (mcp.NewInMemoryTransports):
// a tool that asks for a confirmation the way lumen-ops will (an InputRequiredResult with an elicitation
// request), plus a plain tool. Go-vs-Python output parity is tested from Python
// (mcp/python/tests/test_client.py builds this binary and compares it with client.py).

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const declined = "label not written: the user declined"

type labelIn struct {
	ID string `json:"id"`
}

func text(s string, isErr bool) *mcp.CallToolResult {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: s}}, IsError: isErr}
}

// askingServer: round one returns an elicitation request; round two reads the answer.
func askingServer(asked *int) *mcp.Server {
	s := mcp.NewServer(&mcp.Implementation{Name: "asker", Version: "0"}, nil)
	mcp.AddTool(s, &mcp.Tool{Name: "label_ticket", Description: "asks first"},
		func(ctx context.Context, req *mcp.CallToolRequest, in labelIn) (*mcp.CallToolResult, any, error) {
			resp, ok := req.Params.InputResponses["confirm"]
			if !ok {
				*asked++
				return &mcp.CallToolResult{
					InputRequests: mcp.InputRequestMap{"confirm": &mcp.ElicitParams{Mode: "form",
						Message: "Write a label on ticket " + in.ID + "?",
						RequestedSchema: map[string]any{"type": "object",
							"properties": map[string]any{"confirm": map[string]any{"type": "boolean"}},
							"required":   []string{"confirm"}}}},
					RequestState: "round-1",
				}, nil, nil
			}
			er, _ := resp.(*mcp.ElicitResult)
			if er == nil || er.Action != "accept" || er.Content["confirm"] != true {
				return text(declined, true), nil, nil
			}
			return text("labelled "+in.ID, false), nil, nil
		})
	return s
}

func session(t *testing.T, server *mcp.Server, answer Answerer) *mcp.ClientSession {
	t.Helper()
	ctx := context.Background()
	st, ct := mcp.NewInMemoryTransports()
	ss, err := server.Connect(ctx, st, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ss.Close() })
	cs, err := connect(ctx, ct, answer)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cs.Close() })
	return cs
}

func callLabel(t *testing.T, answer Answerer) (map[string]any, int) {
	t.Helper()
	asked := 0
	cs := session(t, askingServer(&asked), answer)
	out, err := call(context.Background(), cs, "label_ticket", map[string]any{"id": "t001"})
	if err != nil {
		t.Fatal(err)
	}
	return out.(map[string]any), asked
}

// ── 5. Input-required rounds ────────────────────────────────────────────────
func TestElicitationYesAccepts(t *testing.T) {
	got, asked := callLabel(t, always(true))
	if !reflect.DeepEqual(got, map[string]any{"structured": nil}) || asked != 1 {
		t.Fatalf("got %v asked %d", got, asked)
	}
}

func TestElicitationNoDeclines(t *testing.T) {
	got, _ := callLabel(t, always(false))
	if !reflect.DeepEqual(got, map[string]any{"error": declined}) {
		t.Fatalf("got %v", got)
	}
}

func TestElicitationTerminalPromptGoesToStderr(t *testing.T) {
	for _, c := range []struct {
		typed    string
		accepted bool
	}{{"y\n", true}, {"YES\n", true}, {"n\n", false}, {"\n", false}, {"", false}} {
		var stderr bytes.Buffer
		got, _ := callLabel(t, askTerminal(strings.NewReader(c.typed), &stderr))
		_, isError := got["error"]
		if isError == c.accepted {
			t.Errorf("typed %q: got %v", c.typed, got)
		}
		if stderr.String() != "Write a label on ticket t001? [y/N] " {
			t.Errorf("typed %q: stderr %q", c.typed, stderr.String())
		}
	}
}

func TestUnanswerableFormIsDeclined(t *testing.T) {
	h := elicitationHandler(always(true))
	res, err := h(context.Background(), &mcp.ElicitRequest{Params: &mcp.ElicitParams{
		Message:         "name?",
		RequestedSchema: map[string]any{"type": "object", "properties": map[string]any{"name": map[string]any{"type": "string"}}},
	}})
	if err != nil || res.Action != "decline" {
		t.Fatalf("got %v %v", res, err)
	}
}

// ── 3. Discovery ────────────────────────────────────────────────────────────
func TestNormaliseSchemaDropsDecoration(t *testing.T) {
	var schema any
	json.Unmarshal([]byte(`{"$defs": {"Hit": {"title": "Hit", "type": "object", "required": ["b", "a"],
		"properties": {"a": {"title": "A", "type": "string"}, "b": {"type": ["integer", "null"], "default": 1}}}},
		"title": "Out", "type": "object", "additionalProperties": false,
		"properties": {"hits": {"type": "array", "items": {"$ref": "#/$defs/Hit"}},
			"m": {"type": "object", "additionalProperties": {"type": "number"}}}}`), &schema)
	var want any
	json.Unmarshal([]byte(`{"type": "object", "properties": {
		"hits": {"type": "array", "items": {"type": "object", "required": ["a", "b"],
			"properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}},
		"m": {"type": "object", "additionalProperties": {"type": "number"}}}}`), &want)
	if got := generic(normaliseSchema(schema, nil)); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v\nwant %v", got, want)
	}
}

func TestToolsListSortedWithoutDecoration(t *testing.T) {
	asked := 0
	cs := session(t, askingServer(&asked), always(false))
	out, err := listTools(context.Background(), cs)
	if err != nil {
		t.Fatal(err)
	}
	tools := out.([]any)
	first := tools[0].(map[string]any)
	if first["name"] != "label_ticket" || first["title"] != nil || first["annotations"] != nil {
		t.Fatalf("got %v", first)
	}
	if !reflect.DeepEqual(first["inputSchema"], map[string]any{"type": "object", "required": []string{"id"},
		"properties": map[string]any{"id": map[string]any{"type": "string"}}}) {
		t.Fatalf("schema %v", first["inputSchema"])
	}
}

// ── 6. Entry point ──────────────────────────────────────────────────────────
func TestDumpIsAsciiSortedAndIndented(t *testing.T) {
	got := dump(map[string]any{"b": "café \U0001F600 <&>", "a": []int{1}})
	want := "{\n  \"a\": [\n    1\n  ],\n  \"b\": \"caf\\u00e9 \\ud83d\\ude00 <&>\"\n}"
	if got != want {
		t.Fatalf("got %s\nwant %s", got, want)
	}
}
