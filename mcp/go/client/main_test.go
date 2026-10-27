package main

// The tests drive the client's functions against an in-process test server (mcp.NewInMemoryTransports):
// a tool that asks for a confirmation the way lumen-ops will (an InputRequiredResult with an elicitation
// request), plus a plain tool. Go-vs-Python output parity is tested from Python
// (mcp/python/tests/test_client.py builds this binary and compares it with client.py).

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
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

// ── 2. HTTP (-url) ──────────────────────────────────────────────────────────

// plainServer has a tool that answers with the structured result, a tool whose error has no content,
// a resource whose text is not JSON, and a prompt that takes one argument.
func plainServer() *mcp.Server {
	s := mcp.NewServer(&mcp.Implementation{Name: "plain", Version: "0"}, nil)
	mcp.AddTool(s, &mcp.Tool{Name: "echo", Description: "echoes the id"},
		func(ctx context.Context, req *mcp.CallToolRequest, in labelIn) (*mcp.CallToolResult, any, error) {
			return nil, map[string]any{"id": in.ID}, nil
		})
	s.AddTool(&mcp.Tool{Name: "silent_fail", InputSchema: map[string]any{"type": "object"}},
		func(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			return &mcp.CallToolResult{IsError: true}, nil
		})
	s.AddResource(&mcp.Resource{URI: "plain://doc", Name: "doc", MIMEType: "text/plain"},
		func(ctx context.Context, req *mcp.ReadResourceRequest) (*mcp.ReadResourceResult, error) {
			return &mcp.ReadResourceResult{Contents: []*mcp.ResourceContents{{URI: "plain://doc", MIMEType: "text/plain", Text: "not json"}}}, nil
		})
	s.AddPrompt(&mcp.Prompt{Name: "p", Arguments: []*mcp.PromptArgument{{Name: "id"}}},
		func(ctx context.Context, req *mcp.GetPromptRequest) (*mcp.GetPromptResult, error) {
			return &mcp.GetPromptResult{Messages: []*mcp.PromptMessage{{Role: "user", Content: &mcp.TextContent{Text: "hi " + req.Params.Arguments["id"]}}}}, nil
		})
	return s
}

func TestURLSmoke(t *testing.T) {
	srv := httptest.NewServer(mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return plainServer() },
		&mcp.StreamableHTTPOptions{Stateless: true}))
	defer srv.Close()
	if !strings.HasPrefix(srv.URL, "http://127.0.0.1:") {
		t.Fatalf("test server not on 127.0.0.1: %s", srv.URL)
	}
	transport, err := newTransport(false, srv.URL, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	cs, err := connect(ctx, transport, always(false))
	if err != nil {
		t.Fatal(err)
	}
	defer cs.Close()
	tools, err := run(ctx, cs, "tools", nil)
	if err != nil || len(tools.([]any)) != 2 {
		t.Fatalf("tools: %v %v", tools, err)
	}
	got, err := run(ctx, cs, "call", []string{"echo", `{"id": "t9"}`})
	if err != nil || !reflect.DeepEqual(got, map[string]any{"structured": map[string]any{"id": "t9"}}) {
		t.Fatalf("call: %v %v", got, err)
	}
}

func TestTokenSentAsBearerHeaderOnly(t *testing.T) {
	// R19: -token becomes `Authorization: Bearer <token>` on every request, never part of the URL.
	const token = "tok-123.abc"
	inner := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return plainServer() },
		&mcp.StreamableHTTPOptions{Stateless: true})
	var mu sync.Mutex
	var auths, urls []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		auths, urls = append(auths, r.Header.Get("Authorization")), append(urls, r.URL.String())
		mu.Unlock()
		inner.ServeHTTP(w, r)
	}))
	defer srv.Close()
	transport, err := newTransport(false, srv.URL+"/mcp", token, nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	cs, err := connect(ctx, transport, always(false))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := run(ctx, cs, "tools", nil); err != nil {
		t.Fatal(err)
	}
	cs.Close()
	mu.Lock()
	defer mu.Unlock()
	if len(auths) == 0 {
		t.Fatal("no requests seen")
	}
	for i := range auths {
		if auths[i] != "Bearer "+token || strings.Contains(urls[i], token) {
			t.Errorf("request %d: Authorization %q, URL %q", i, auths[i], urls[i])
		}
	}
	if _, err := newTransport(true, "", token, nil); err == nil {
		t.Error("-token with -stdio must be refused")
	}
}

func TestTokenNeverLeavesTheEndpointOrigin(t *testing.T) {
	// A server that answers every request with a 307 to another origin (a different port: the
	// "third party"). The third party must never see the Authorization header, and the client must
	// not follow the redirect at all.
	const token = "SECRET-TOKEN"
	var mu sync.Mutex
	var thirdPartyAuth []string
	third := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		thirdPartyAuth = append(thirdPartyAuth, r.Header.Get("Authorization"))
		mu.Unlock()
		w.WriteHeader(http.StatusTeapot)
	}))
	defer third.Close()
	// localhost vs 127.0.0.1 and a different port: a different origin either way.
	target := strings.Replace(third.URL, "127.0.0.1", "localhost", 1) + "/mcp"
	var endpointAuth []string
	redirector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		endpointAuth = append(endpointAuth, r.Header.Get("Authorization"))
		mu.Unlock()
		http.Redirect(w, r, target, http.StatusTemporaryRedirect)
	}))
	defer redirector.Close()
	rec := &refusal{}
	transport, err := newTransport(false, redirector.URL+"/mcp", token, rec)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connect(context.Background(), transport, always(false)); err == nil {
		t.Fatal("a redirected endpoint must fail the connection")
	}
	if rec.get() != http.StatusTemporaryRedirect ||
		refusalLine(rec.get()) != "client: the server refused the request: HTTP 307 Temporary Redirect" {
		t.Errorf("refusal %d", rec.get())
	}
	mu.Lock()
	defer mu.Unlock()
	if len(thirdPartyAuth) != 0 {
		t.Fatalf("the redirect was followed: third party saw %q", thirdPartyAuth)
	}
	if len(endpointAuth) == 0 || endpointAuth[0] != "Bearer "+token {
		t.Fatalf("the endpoint itself must get the token: %q", endpointAuth)
	}
}

func TestBearerOnlyForTheEndpointOrigin(t *testing.T) {
	// Even if something did send a request elsewhere through this client, no header is added.
	var seen []string
	rt := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		seen = append(seen, r.URL.String()+" "+r.Header.Get("Authorization"))
		return &http.Response{StatusCode: 200, Body: http.NoBody, Request: r}, nil
	})
	origin, _ := url.Parse("http://127.0.0.1:8765/mcp")
	b := bearer{"T", origin, rt}
	for _, u := range []string{"http://127.0.0.1:8765/mcp", "http://127.0.0.1:8766/mcp",
		"http://localhost:8765/mcp", "https://127.0.0.1:8765/mcp", "http://evil.example/mcp"} {
		req, _ := http.NewRequest("GET", u, nil)
		b.RoundTrip(req)
	}
	want := []string{"http://127.0.0.1:8765/mcp Bearer T", "http://127.0.0.1:8766/mcp ",
		"http://localhost:8765/mcp ", "https://127.0.0.1:8765/mcp ", "http://evil.example/mcp "}
	if !reflect.DeepEqual(seen, want) {
		t.Fatalf("got %q", seen)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestResolveTokenPrecedence(t *testing.T) {
	f := filepath.Join(t.TempDir(), "t.tok")
	os.WriteFile(f, []byte("  from-file \nsecond\n"), 0o600)
	for _, c := range []struct {
		file, flag, env, want string
		warn                  bool
	}{
		{f, "from-flag", "from-env", "from-file", true},
		{"", "from-flag", "from-env", "from-flag", true},
		{"", "", "from-env", "from-env", false},
		{"", "", "", "", false},
	} {
		got, warn, err := resolveToken(c.file, c.flag, c.env)
		if err != nil || got != c.want || warn != c.warn {
			t.Errorf("%+v: got %q %v %v", c, got, warn, err)
		}
	}
	empty := filepath.Join(t.TempDir(), "e.tok")
	os.WriteFile(empty, []byte("\n"), 0o600)
	if _, _, err := resolveToken(empty, "", ""); err == nil || err.Error() != "token file is empty: "+empty {
		t.Errorf("empty: %v", err)
	}
	if _, _, err := resolveToken(empty+".missing", "", ""); err == nil || err.Error() != "token file not readable: "+empty+".missing" {
		t.Errorf("missing: %v", err)
	}
}

// ── 4. Edge cases ───────────────────────────────────────────────────────────
func TestEdgeCases(t *testing.T) {
	ctx := context.Background()
	cs := session(t, plainServer(), always(false))
	if got, _ := call(ctx, cs, "silent_fail", map[string]any{}); !reflect.DeepEqual(got, map[string]any{"error": "<no text>"}) {
		t.Errorf("empty error content: %v", got)
	}
	got, _ := read(ctx, cs, "plain://doc")
	entry := got.(map[string]any)["contents"].([]any)[0]
	if !reflect.DeepEqual(entry, map[string]any{"uri": "plain://doc", "mimeType": "text/plain", "text": "not json"}) {
		t.Errorf("non-JSON resource: %v", entry)
	}
	if _, err := prompt(ctx, cs, "p", map[string]any{"id": 1}); err == nil || err.Error() != "prompt arguments must be strings: id" {
		t.Errorf("non-string prompt argument: %v", err)
	}
	if _, err := run(ctx, cs, "call", nil); err == nil || err.Error() != "call needs 1 argument(s)" {
		t.Errorf("missing args: %v", err)
	}
	for _, bad := range []string{"[1]", "null", "nope"} {
		if _, err := parseObject([]string{bad}, 0); err == nil || err.Error() != "arguments are not a JSON object" {
			t.Errorf("%s: %v", bad, err)
		}
	}
}
