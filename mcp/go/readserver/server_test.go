package main

// Test transport: go-sdk v1.8.0 exposes a public in-memory transport (mcp.NewInMemoryTransports),
// so TestContractRead runs the server and a real client in-process, with no sockets (and no bearer
// check: auth lives in the HTTP layer). The HTTP tests drive the handler directly with
// net/http/httptest, with a token from authtest (lumen-read's own: tickets:read, read audience).

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"lumen-mcp/auth"
	"lumen-mcp/auth/authtest"
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
	return map[string]any{"error": je.Message, "code": float64(je.Code)}
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

// readToken() is the HTTP fixture token: lumen-read's own scope and audience.
func readToken() string { return tok(auth.ScopeRead, auth.ReadResource, "lumen-cli", "user-1") }

// httpHandler gives this test its own random signing key (ruling R25) and the server's HTTP handler.
func httpHandler(t *testing.T, s *mcp.Server) http.Handler {
	t.Helper()
	authtest.UseKey(t)
	h, err := NewHTTPHandler(s, DefaultPort)
	if err != nil {
		t.Fatal(err)
	}
	return h
}

// tok mints a token with this test's key (set by httpHandler / authtest.UseKey).
func tok(scope, audience, clientID, sub string) string {
	key, err := auth.LoadSigningKey()
	if err != nil {
		panic(err)
	}
	return authtest.Token(key, scope, audience, clientID, sub)
}

func newReqAs(method string, body io.Reader, origin, token string) *http.Request {
	req := httptest.NewRequest(method, "http://127.0.0.1:8765/mcp", body)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	return req
}

func newReq(body io.Reader, origin string) *http.Request {
	return newReqAs("POST", body, origin, readToken())
}

func post(h http.Handler, body, origin string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, newReq(strings.NewReader(body), origin))
	return rec
}

func postAs(h http.Handler, token string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, newReqAs("POST", strings.NewReader(listBody), "", token))
	return rec
}

func TestOriginRejected(t *testing.T) {
	h := httpHandler(t, NewReadServer())
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
	h := httpHandler(t, NewReadServer())
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

// ── 4. Authorization (lumen-read over HTTP is a resource server) ────────────
func TestNoToken401WithMetadata(t *testing.T) {
	rec := postAs(httpHandler(t, NewReadServer()), "")
	want := `resource_metadata="http://127.0.0.1:8765/.well-known/oauth-protected-resource/mcp"`
	if rec.Code != 401 || !strings.Contains(rec.Header().Get("WWW-Authenticate"), want) {
		t.Fatalf("got %d %q", rec.Code, rec.Header().Get("WWW-Authenticate"))
	}
}

func TestRightTokenOK(t *testing.T) {
	if rec := postAs(httpHandler(t, NewReadServer()), readToken()); rec.Code != 200 {
		t.Fatalf("got %d %s", rec.Code, rec.Body.String())
	}
}

func TestWrongAudience401(t *testing.T) {
	h := httpHandler(t, NewReadServer())
	for _, aud := range []string{auth.OpsResource, "http://127.0.0.1:9999/mcp"} {
		if rec := postAs(h, tok(auth.ScopeRead, aud, "lumen-cli", "user-1")); rec.Code != 401 {
			t.Errorf("aud %s: got %d", aud, rec.Code)
		}
	}
}

func TestWrongScope403(t *testing.T) {
	h := httpHandler(t, NewReadServer())
	for _, scope := range []string{auth.ScopeWrite, "", "tickets:admin"} {
		rec := postAs(h, tok(scope, auth.ReadResource, "lumen-cli", "user-1"))
		wa := rec.Header().Get("WWW-Authenticate")
		if rec.Code != 403 || !strings.Contains(wa, "resource_metadata=") || !strings.Contains(wa, `error="insufficient_scope"`) {
			t.Errorf("scope %q: got %d %q", scope, rec.Code, wa)
		}
	}
}

func TestProtectedResourceMetadata(t *testing.T) {
	rec := httptest.NewRecorder()
	httpHandler(t, NewReadServer()).ServeHTTP(rec,
		httptest.NewRequest("GET", "http://127.0.0.1:8765/.well-known/oauth-protected-resource/mcp", nil))
	var prm struct {
		Resource             string   `json:"resource"`
		AuthorizationServers []string `json:"authorization_servers"`
		ScopesSupported      []string `json:"scopes_supported"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &prm); err != nil {
		t.Fatal(err)
	}
	if prm.Resource != auth.ReadResource || prm.AuthorizationServers[0] != auth.Issuer || prm.ScopesSupported[0] != auth.ScopeRead {
		t.Errorf("PRM %+v", prm)
	}
}

func TestGetWithBadOrigin403(t *testing.T) {
	// R18: Origin is checked for every method, not only the unsafe ones (with a valid token).
	h := httpHandler(t, NewReadServer())
	for origin, bad := range map[string]bool{"http://evil.example": true, "http://127.0.0.1:6274": false} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, newReqAs("GET", nil, origin, readToken()))
		if (rec.Code == 403) != bad {
			t.Errorf("GET Origin %q: got %d", origin, rec.Code)
		}
	}
}

func TestOriginCheckedBeforeAuth(t *testing.T) {
	// R42 ordering: Host and Origin are checked first, so a bad Origin is 403 even with no token.
	// (Python now uses the same order.)
	rec := httptest.NewRecorder()
	httpHandler(t, NewReadServer()).ServeHTTP(rec, newReqAs("POST", strings.NewReader(listBody), "http://evil.example", ""))
	if rec.Code != 403 {
		t.Fatalf("got %d", rec.Code)
	}
}

// serveLocal starts a real HTTP server for s on a free 127.0.0.1 port, with the handler built for that
// port (its Host allow-list names it, ruling R42), and returns it.
func serveLocal(t *testing.T, s *mcp.Server) *httptest.Server {
	t.Helper()
	authtest.UseKey(t)
	ts := httptest.NewUnstartedServer(nil)
	port := ts.Listener.Addr().(*net.TCPAddr).Port
	h, err := NewHTTPHandler(s, port)
	if err != nil {
		t.Fatal(err)
	}
	ts.Config.Handler = h
	ts.Start()
	t.Cleanup(ts.Close)
	return ts
}

// rebindingStatus sends one tools/list to ts with the given Host and Origin and a valid token.
func rebindingStatus(t *testing.T, ts *httptest.Server, host, origin, token string) int {
	t.Helper()
	req, err := http.NewRequest("POST", ts.URL+"/mcp", strings.NewReader(listBody))
	if err != nil {
		t.Fatal(err)
	}
	req.Host = host
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Authorization", "Bearer "+token)
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	return resp.StatusCode
}

func TestDNSRebindingRefusedOverHTTP(t *testing.T) {
	// R42, on a real server: Host and Origin both name the attacker's host. 403 even with a valid token.
	ts := serveLocal(t, NewReadServer())
	port := ts.Listener.Addr().(*net.TCPAddr).Port
	token := readToken()
	at := func(h string) string { return fmt.Sprintf("%s:%d", h, port) }
	for _, c := range []struct {
		host, origin string
		want         int
	}{
		{at("evil.invalid"), "http://" + at("evil.invalid"), 403},
		{at("evil.invalid"), "", 403},
		{at("127.0.0.1"), "http://" + at("evil.invalid"), 403},
		{at("127.0.0.1"), "", 200},
		{at("127.0.0.1"), "http://" + at("127.0.0.1"), 200},
		{at("localhost"), "http://" + at("localhost"), 200},
		{at("127.0.0.1"), "http://127.0.0.1:6274", 200},
		{at("127.0.0.1"), "http://localhost:6274", 200},
	} {
		if got := rebindingStatus(t, ts, c.host, c.origin, token); got != c.want {
			t.Errorf("Host %q Origin %q: %d, want %d", c.host, c.origin, got, c.want)
		}
	}
}
