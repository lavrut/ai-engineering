package main

// Test transport: go-sdk v1.8.0 exposes a public in-memory transport (mcp.NewInMemoryTransports),
// so TestContractRead runs the server and a real client in-process, with no sockets. The two HTTP
// hardening tests drive the handler directly with net/http/httptest, because Origin and body-cap
// behaviour lives in the HTTP layer that the in-memory transport skips.

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"lumen-mcp/core"
)

// ── 1. Contract compare (the rules in mcp/fixtures/README.md) ───────────────
type contractCase struct {
	Name   string          `json:"name"`
	Server string          `json:"server"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
	Expect map[string]any  `json:"expect"`
}

func loadCases(t *testing.T, server string) []contractCase {
	t.Helper()
	f, err := os.Open(filepath.Join(core.Root(), "mcp", "fixtures", "contract.jsonl"))
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
		if c.Server == server {
			out = append(out, c)
		}
	}
	return out
}

// equal is deep equality on parsed JSON: floats within an absolute 1e-9, everything else exact.
// Parsed JSON has only float64, string, bool, nil, []any and map[string]any.
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

func rpcMessage(t *testing.T, err error) map[string]any {
	t.Helper()
	var je *jsonrpc.Error
	if !errors.As(err, &je) {
		t.Fatalf("want a JSON-RPC error, got %T: %v", err, err)
	}
	return map[string]any{"error": je.Message}
}

func connect(t *testing.T) *mcp.ClientSession {
	t.Helper()
	ctx := context.Background()
	st, ct := mcp.NewInMemoryTransports()
	ss, err := NewReadServer().Connect(ctx, st, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ss.Close() })
	cs, err := mcp.NewClient(&mcp.Implementation{Name: "contract-test", Version: "0"}, nil).Connect(ctx, ct, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cs.Close() })
	return cs
}

// run turns one case into the `expect` shape the README defines.
func run(t *testing.T, cs *mcp.ClientSession, c contractCase) map[string]any {
	t.Helper()
	ctx := context.Background()
	switch c.Method {
	case "tools/call":
		var p struct {
			Name      string         `json:"name"`
			Arguments map[string]any `json:"arguments"`
		}
		if err := json.Unmarshal(c.Params, &p); err != nil {
			t.Fatal(err)
		}
		res, err := cs.CallTool(ctx, &mcp.CallToolParams{Name: p.Name, Arguments: p.Arguments})
		if err != nil {
			t.Fatalf("CallTool: %v", err)
		}
		if res.IsError {
			if res.StructuredContent != nil {
				t.Errorf("error result must carry no structuredContent: %v", res.StructuredContent)
			}
			return map[string]any{"error": res.Content[0].(*mcp.TextContent).Text}
		}
		return map[string]any{"structured": generic(t, res.StructuredContent)}
	case "resources/read":
		var p struct {
			URI string `json:"uri"`
		}
		json.Unmarshal(c.Params, &p)
		res, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: p.URI})
		if err != nil {
			return rpcMessage(t, err)
		}
		contents := []any{}
		for _, rc := range res.Contents {
			var j any
			if err := json.Unmarshal([]byte(rc.Text), &j); err != nil {
				t.Fatalf("resource text is not JSON: %v", err)
			}
			contents = append(contents, map[string]any{"uri": rc.URI, "mimeType": rc.MIMEType, "json": j})
		}
		return map[string]any{"contents": contents}
	case "prompts/get":
		var p struct {
			Name      string            `json:"name"`
			Arguments map[string]string `json:"arguments"`
		}
		json.Unmarshal(c.Params, &p)
		res, err := cs.GetPrompt(ctx, &mcp.GetPromptParams{Name: p.Name, Arguments: p.Arguments})
		if err != nil {
			return rpcMessage(t, err)
		}
		contents := []any{}
		for _, m := range res.Messages {
			contents = append(contents, map[string]any{"role": string(m.Role), "text": m.Content.(*mcp.TextContent).Text})
		}
		return map[string]any{"contents": contents}
	}
	t.Fatalf("unknown method %q", c.Method)
	return nil
}

func TestContractRead(t *testing.T) {
	cases := loadCases(t, "read")
	if len(cases) < 30 {
		t.Fatalf("only %d read cases loaded", len(cases))
	}
	cs := connect(t)
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			got := run(t, cs, c)
			if !equal(got, generic(t, c.Expect)) {
				g, _ := json.Marshal(got)
				w, _ := json.Marshal(c.Expect)
				t.Fatalf("got  %.600s\nwant %.600s", g, w)
			}
		})
	}
}

// ── 2. Tool metadata ────────────────────────────────────────────────────────
func TestToolsReadOnlyAndInstructions(t *testing.T) {
	cs := connect(t)
	if got := cs.InitializeResult().Instructions; got != Instructions {
		t.Errorf("instructions: %q", got)
	}
	n := 0
	for tool, err := range cs.Tools(context.Background(), nil) {
		if err != nil {
			t.Fatal(err)
		}
		n++
		if tool.Annotations == nil || !tool.Annotations.ReadOnlyHint {
			t.Errorf("%s: readOnlyHint must be true", tool.Name)
		}
	}
	if n != 3 {
		t.Errorf("want 3 tools, got %d", n)
	}
}

// ── 3. HTTP hardening ───────────────────────────────────────────────────────
const listBody = `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`

func newReq(body io.Reader, origin string) *http.Request {
	req := httptest.NewRequest("POST", "http://127.0.0.1:8765/mcp", body)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	return req
}

func post(h http.Handler, body, origin string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, newReq(strings.NewReader(body), origin))
	return rec
}

func TestOriginRejected(t *testing.T) {
	h := NewHTTPHandler(NewReadServer())
	for origin, want := range map[string]int{
		"http://evil.example":   http.StatusForbidden,
		"http://127.0.0.1:9999": http.StatusForbidden,
		"http://127.0.0.1:6274": http.StatusOK, // Inspector (R11)
		"http://localhost:6274": http.StatusOK,
		"http://127.0.0.1:8765": http.StatusOK, // the server's own origin
		"":                      http.StatusOK, // non-browser client
	} {
		if got := post(h, listBody, origin).Code; got != want {
			t.Errorf("Origin %q: got %d, want %d", origin, got, want)
		}
	}
}

// padded is a valid JSON-RPC request padded with trailing spaces to exactly n bytes.
func padded(n int) string { return listBody + strings.Repeat(" ", n-len(listBody)) }

func TestBodyCap(t *testing.T) {
	h := NewHTTPHandler(NewReadServer())
	if got := post(h, padded(core.MaxBodyBytes), "").Code; got != http.StatusOK {
		t.Errorf("exactly %d bytes: got %d, want 200", core.MaxBodyBytes, got)
	}
	if got := post(h, padded(core.MaxBodyBytes+1), "").Code; got != http.StatusRequestEntityTooLarge {
		t.Errorf("%d bytes: got %d, want 413", core.MaxBodyBytes+1, got)
	}
	// A chunked body has no Content-Length; MaxBytesReader must still stop it.
	req := newReq(io.NopCloser(bytes.NewReader([]byte(padded(core.MaxBodyBytes+1)))), "")
	req.ContentLength = -1
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("chunked %d bytes: got %d, want 413 (%s)", core.MaxBodyBytes+1, rec.Code, rec.Body.String())
	}
}
