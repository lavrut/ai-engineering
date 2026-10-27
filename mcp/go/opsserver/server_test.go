package main

// Test transport: the in-memory transport (mcp.NewInMemoryTransports) runs the server and a real Go
// client in-process for the contract and the confirmation tests. The legacy-era test and the HTTP
// hardening and authorization tests use the real Stateless HTTP handler (httptest), because that is
// where ruling R6, the Origin/body-cap checks and the bearer check live; they carry a token from
// authtest (lumen-ops's own unless a test says otherwise). Every test writes to a t.TempDir() labels file.

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"lumen-mcp/auth"
	"lumen-mcp/auth/authtest"
	"lumen-mcp/core"
)

// ── 1. Helpers ──────────────────────────────────────────────────────────────
var accept = &mcp.ElicitResult{Action: "accept", Content: map[string]any{"confirm": true}}

type asked struct {
	Message         string `json:"message"`
	RequestedSchema any    `json:"requestedSchema"`
}

// replying is an elicitation handler that records what it was shown and answers reply
// (nil: it must not be asked; if it is, it records that and declines).
func replying(reply *mcp.ElicitResult, seen *[]asked) func(context.Context, *mcp.ElicitRequest) (*mcp.ElicitResult, error) {
	return func(_ context.Context, req *mcp.ElicitRequest) (*mcp.ElicitResult, error) {
		*seen = append(*seen, asked{req.Params.Message, generic(req.Params.RequestedSchema)})
		if reply == nil {
			return &mcp.ElicitResult{Action: "decline"}, nil
		}
		return reply, nil
	}
}

func labelsFile(t *testing.T) string { return filepath.Join(t.TempDir(), "labels.jsonl") }

func readRows(t *testing.T, path string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		if line == "" {
			continue
		}
		var row map[string]any
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatalf("bad row %q: %v", line, err)
		}
		out = append(out, row)
	}
	return out
}

// connectOps connects a client to a fresh ops server in memory. opts may disable the SDK's
// automatic retry so a test can drive the rounds by hand.
func connectOps(t *testing.T, srv *mcp.Server, copts *mcp.ClientOptions) *mcp.ClientSession {
	t.Helper()
	ctx := context.Background()
	st, ct := mcp.NewInMemoryTransports()
	ss, err := srv.Connect(ctx, st, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ss.Close() })
	cs, err := mcp.NewClient(&mcp.Implementation{Name: "ops-test", Version: "0"}, copts).Connect(ctx, ct, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cs.Close() })
	return cs
}

func callLabel(t *testing.T, path string, id, label string, reply *mcp.ElicitResult) (*mcp.CallToolResult, []asked) {
	t.Helper()
	var seen []asked
	cs := connectOps(t, NewOpsServer(path), &mcp.ClientOptions{ElicitationHandler: replying(reply, &seen)})
	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "label_ticket",
		Arguments: map[string]any{"id": id, "label": label}})
	if err != nil {
		t.Fatalf("CallTool: %v", err)
	}
	return res, seen
}

func textOf(res *mcp.CallToolResult) string { return res.Content[0].(*mcp.TextContent).Text }

func wantDeclined(t *testing.T, res *mcp.CallToolResult) {
	t.Helper()
	if !res.IsError || textOf(res) != Declined {
		t.Fatalf("want the fixed decline, got isError=%v %q", res.IsError, textOf(res))
	}
}

// ── 2. The confirmed write and its provenance ───────────────────────────────
func TestLabelAcceptedWritesOneRow(t *testing.T) {
	path := labelsFile(t)
	res, seen := callLabel(t, path, "t001", "billing", accept)
	if res.IsError {
		t.Fatalf("error: %s", textOf(res))
	}
	if got := generic(res.StructuredContent); !reflect.DeepEqual(got, map[string]any{"ticket_id": "t001", "label": "billing", "written": true}) {
		t.Errorf("structured: %v", got)
	}
	wantAsked := []asked{{`Set ticket t001 to "billing"?`, map[string]any{"type": "object", "required": []any{"confirm"},
		"properties": map[string]any{"confirm": map[string]any{"type": "boolean", "title": "Confirm"}}}}}
	if !reflect.DeepEqual(seen, wantAsked) {
		t.Errorf("asked %#v", seen)
	}
	rows := readRows(t, path)
	if len(rows) != 1 {
		t.Fatalf("want 1 row, got %d", len(rows))
	}
	tm, _ := rows[0]["time"].(string)
	delete(rows[0], "time")
	want := map[string]any{"ticket_id": "t001", "label": "billing", "source": "agent", "client_id": "anonymous",
		"model": nil, "confirmed": true}
	if !reflect.DeepEqual(rows[0], want) || !strings.HasSuffix(tm, "+00:00") {
		t.Errorf("row %v time %q", rows[0], tm)
	}
}

func TestLabelDeclinedWritesNothing(t *testing.T) {
	for name, reply := range map[string]*mcp.ElicitResult{
		"decline":       {Action: "decline"},
		"cancel":        {Action: "cancel"},
		"confirm_false": {Action: "accept", Content: map[string]any{"confirm": false}},
		"no_content":    {Action: "accept"},
	} {
		t.Run(name, func(t *testing.T) {
			path := labelsFile(t)
			res, seen := callLabel(t, path, "t001", "bug", reply)
			wantDeclined(t, res)
			if res.StructuredContent != nil || len(seen) != 1 || len(readRows(t, path)) != 0 {
				t.Errorf("structured %v, asked %d, rows %d", res.StructuredContent, len(seen), len(readRows(t, path)))
			}
		})
	}
}

func TestLabelBadLabel(t *testing.T) {
	path := labelsFile(t)
	res, seen := callLabel(t, path, "t001", "spam", accept)
	if !res.IsError || textOf(res) != "label must be one of: billing, bug, account, how_to, feature_request, outage" {
		t.Fatalf("got %q", textOf(res))
	}
	if len(seen) != 0 || len(readRows(t, path)) != 0 {
		t.Errorf("asked %d, rows %d: validation must come before any question", len(seen), len(readRows(t, path)))
	}
}

func TestLabelUnknownTicket(t *testing.T) {
	path := labelsFile(t)
	res, seen := callLabel(t, path, "t999", "billing", accept)
	if !res.IsError || textOf(res) != "unknown ticket: t999" {
		t.Fatalf("got %q", textOf(res))
	}
	if len(seen) != 0 || len(readRows(t, path)) != 0 {
		t.Errorf("asked %d, rows %d", len(seen), len(readRows(t, path)))
	}
}

func TestToolMetadata(t *testing.T) {
	cs := connectOps(t, NewOpsServer(labelsFile(t)), nil)
	n := 0
	for tool, err := range cs.Tools(context.Background(), nil) {
		if err != nil {
			t.Fatal(err)
		}
		n++
		if tool.Name != "label_ticket" || tool.Annotations == nil || tool.Annotations.ReadOnlyHint ||
			tool.Annotations.DestructiveHint == nil || *tool.Annotations.DestructiveHint || tool.OutputSchema == nil {
			t.Errorf("tool %s annotations %+v", tool.Name, tool.Annotations)
		}
	}
	if n != 1 {
		t.Errorf("want 1 tool, got %d", n)
	}
	if got := newOps("").labelsPath; got != core.LabelsPath() {
		t.Errorf("\"\" must mean the default labels path, got %q", got)
	}
}

// ── 3. RequestState: forged, rebound, expired and replayed rounds write nothing ─
// manual drives the rounds by hand: the SDK's automatic retry is off.
func manual(t *testing.T, srv *mcp.Server) *mcp.ClientSession {
	var seen []asked
	return connectOps(t, srv, &mcp.ClientOptions{ElicitationHandler: replying(accept, &seen),
		MultiRoundTrip: &mcp.MultiRoundTripOptions{Disabled: true}})
}

func round(t *testing.T, cs *mcp.ClientSession, id, label string, answer *mcp.ElicitResult, state string) *mcp.CallToolResult {
	t.Helper()
	p := &mcp.CallToolParams{Name: "label_ticket", Arguments: map[string]any{"id": id, "label": label}, RequestState: state}
	if answer != nil {
		p.InputResponses = mcp.InputResponseMap{"confirm": answer}
	}
	res, err := cs.CallTool(context.Background(), p)
	if err != nil {
		t.Fatalf("CallTool: %v", err)
	}
	return res
}

func round1(t *testing.T, cs *mcp.ClientSession, id, label string) string {
	t.Helper()
	res := round(t, cs, id, label, nil, "")
	if res.InputRequests == nil || res.RequestState == "" {
		t.Fatalf("round 1 must ask: %+v", res)
	}
	return res.RequestState
}

func TestForgedStateWritesNothing(t *testing.T) {
	// Ruling R8: the test asserts no row; Go answers each with the fixed decline.
	path := labelsFile(t)
	cs := manual(t, NewOpsServer(path))
	state := round1(t, cs, "t003", "bug")
	payload, sig, _ := strings.Cut(state, ".")
	raw, _ := base64.RawURLEncoding.DecodeString(payload)
	var cs0 confirmState
	json.Unmarshal(raw, &cs0)
	cs0.Label = "outage" // re-sign nothing: a client cannot, it has no key
	moved, _ := json.Marshal(cs0)
	for name, st := range map[string]string{
		"tampered_sig":   payload + "." + strings.Repeat("A", len(sig)),
		"edited_payload": base64.RawURLEncoding.EncodeToString(moved) + "." + sig,
		"garbage":        "not-a-state",
		"empty":          "",
	} {
		t.Run(name, func(t *testing.T) { wantDeclined(t, round(t, cs, "t003", "bug", accept, st)) })
	}
	wantDeclined(t, round(t, cs, "t003", "outage", accept, state)) // honest state, other arguments
	wantDeclined(t, round(t, cs, "t004", "bug", accept, state))
	if rows := readRows(t, path); len(rows) != 0 {
		t.Fatalf("forged rounds wrote %d rows", len(rows))
	}
}

func TestStateFromAnotherServerWritesNothing(t *testing.T) {
	// Each server process signs with its own random key: a state from one is garbage to another.
	path := labelsFile(t)
	state := round1(t, manual(t, NewOpsServer(path)), "t003", "bug")
	wantDeclined(t, round(t, manual(t, NewOpsServer(path)), "t003", "bug", accept, state))
	if rows := readRows(t, path); len(rows) != 0 {
		t.Fatalf("wrote %d rows", len(rows))
	}
}

func TestExpiredStateWritesNothing(t *testing.T) {
	path := labelsFile(t)
	clock := time.Now()
	o := newOps(path)
	o.now = func() time.Time { return clock }
	cs := manual(t, o.server())
	state := round1(t, cs, "t003", "bug")
	clock = clock.Add(StateTTL + time.Second)
	wantDeclined(t, round(t, cs, "t003", "bug", accept, state))
	if rows := readRows(t, path); len(rows) != 0 {
		t.Fatalf("wrote %d rows", len(rows))
	}
}

func TestReplayedStateWritesOnce(t *testing.T) {
	// Ruling R12: the identical accepted retry, sent twice, writes one row.
	path := labelsFile(t)
	cs := manual(t, NewOpsServer(path))
	state := round1(t, cs, "t004", "outage")
	if res := round(t, cs, "t004", "outage", accept, state); res.IsError {
		t.Fatalf("first retry: %s", textOf(res))
	}
	wantDeclined(t, round(t, cs, "t004", "outage", accept, state))
	fresh := round1(t, cs, "t004", "outage") // a new round 1 gets a new state: allowed
	if fresh == state {
		t.Fatal("two round 1s issued the same state (no nonce)")
	}
	if res := round(t, cs, "t004", "outage", accept, fresh); res.IsError {
		t.Fatalf("fresh confirmation: %s", textOf(res))
	}
	if rows := readRows(t, path); len(rows) != 2 {
		t.Fatalf("want 2 rows (first retry + fresh), got %d", len(rows))
	}
}

func TestDeclinedStateCannotBeReanswered(t *testing.T) {
	path := labelsFile(t)
	cs := manual(t, NewOpsServer(path))
	state := round1(t, cs, "t005", "bug")
	wantDeclined(t, round(t, cs, "t005", "bug", &mcp.ElicitResult{Action: "decline"}, state))
	wantDeclined(t, round(t, cs, "t005", "bug", accept, state))
	if rows := readRows(t, path); len(rows) != 0 {
		t.Fatalf("wrote %d rows", len(rows))
	}
}

func TestNonBooleanConfirmWritesNothing(t *testing.T) {
	// The SDK client checks its own answer against the schema; a hand-rolled client need not.
	// Only the boolean true confirms: "yes", "true" and 1 do not.
	path := labelsFile(t)
	cs := manual(t, NewOpsServer(path))
	for _, v := range []any{"yes", "true", 1} {
		state := round1(t, cs, "t001", "bug")
		wantDeclined(t, round(t, cs, "t001", "bug", &mcp.ElicitResult{Action: "accept", Content: map[string]any{"confirm": v}}, state))
	}
	if rows := readRows(t, path); len(rows) != 0 {
		t.Fatalf("wrote %d rows", len(rows))
	}
}

func TestSpentStatesPruneByTheStatesOwnExpiry(t *testing.T) {
	// R23.3: an entry is dropped only after its own state's expiry plus a margin, never earlier.
	clock := time.Unix(1000, 0)
	s := newSpentStates(func() time.Time { return clock })
	if StateTTL != 5*time.Minute || PruneMargin <= 0 {
		t.Fatalf("StateTTL %v PruneMargin %v", StateTTL, PruneMargin)
	}
	expA := clock.Add(StateTTL)
	if !s.spend("a", expA) || s.spend("a", expA) {
		t.Fatal("first spend must succeed, the replay must not")
	}
	clock = expA.Add(PruneMargin) // inside the margin: still remembered
	if !s.spend("b", clock.Add(StateTTL)) || s.spend("a", expA) || s.size() != 2 {
		t.Fatalf("a must be kept until exp + margin: size %d", s.size())
	}
	clock = expA.Add(PruneMargin + time.Second) // verify rejects "a" as expired anyway
	if !s.spend("c", clock.Add(StateTTL)) || s.size() != 2 {
		t.Fatalf("a must be pruned: size %d", s.size())
	}
}

func TestSpentStatesFollowEachStatesExpiry(t *testing.T) {
	clock := time.Unix(1000, 0)
	s := newSpentStates(func() time.Time { return clock })
	s.spend("late", clock.Add(5*time.Second)) // spent near the end of its life
	s.spend("early", clock.Add(StateTTL))
	clock = clock.Add(5*time.Second + PruneMargin + time.Second)
	s.spend("x", clock.Add(StateTTL))
	if s.spend("early", time.Time{}) || s.size() != 2 {
		t.Fatalf("late must be pruned, early kept: size %d", s.size())
	}
}

func TestConcurrentRetriesWriteOnce(t *testing.T) {
	// R23.5: N identical accepted retries in flight at once write exactly one row (run with -race).
	path := labelsFile(t)
	cs := manual(t, NewOpsServer(path))
	state := round1(t, cs, "t006", "account")
	const n = 20
	var wg sync.WaitGroup
	results := make([]*mcp.CallToolResult, n)
	errs := make([]error, n)
	for i := range n {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i], errs[i] = cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "label_ticket",
				Arguments: map[string]any{"id": "t006", "label": "account"}, RequestState: state,
				InputResponses: mcp.InputResponseMap{"confirm": accept}})
		}()
	}
	wg.Wait()
	written := 0
	for i := range n {
		if errs[i] != nil {
			t.Fatalf("retry %d: %v", i, errs[i])
		}
		if !results[i].IsError {
			written++
		} else if textOf(results[i]) != Declined {
			t.Errorf("retry %d: %q", i, textOf(results[i]))
		}
	}
	if rows := readRows(t, path); written != 1 || len(rows) != 1 {
		t.Fatalf("want exactly 1 write, got %d results ok and %d rows", written, len(rows))
	}
}

// ── 4. Shared contract (the Python server must pass the same file) ──────────
type contractCase struct {
	Name   string          `json:"name"`
	Server string          `json:"server"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
	Answer *struct {
		Action  string         `json:"action"`
		Content map[string]any `json:"content"`
	} `json:"answer"`
	Expect map[string]any `json:"expect"`
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
			if w, present := y[k]; !present || !equal(v, w) {
				return false
			}
		}
		return true
	}
	return reflect.DeepEqual(a, b)
}

// generic round-trips any value through JSON so it compares as parsed JSON.
func generic(v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	var out any
	json.Unmarshal(raw, &out)
	return out
}

// runOps runs one case on a fresh server and labels file, in the README's ops `expect` shape.
func runOps(t *testing.T, c contractCase) map[string]any {
	t.Helper()
	var p struct {
		Name      string            `json:"name"`
		Arguments map[string]string `json:"arguments"`
	}
	if err := json.Unmarshal(c.Params, &p); err != nil {
		t.Fatal(err)
	}
	var reply *mcp.ElicitResult
	if c.Answer != nil {
		reply = &mcp.ElicitResult{Action: c.Answer.Action, Content: c.Answer.Content}
	}
	path := labelsFile(t)
	res, seen := callLabel(t, path, p.Arguments["id"], p.Arguments["label"], reply)
	out := map[string]any{}
	if res.IsError {
		if res.StructuredContent != nil {
			t.Errorf("error result must carry no structuredContent")
		}
		out["error"] = textOf(res)
	} else {
		out["structured"] = generic(res.StructuredContent)
	}
	out["asked"] = nil
	if len(seen) > 1 {
		t.Errorf("asked %d times", len(seen))
	}
	if len(seen) > 0 {
		out["asked"] = generic(seen[0])
	}
	rows := []any{}
	for _, r := range readRows(t, path) {
		delete(r, "time")
		rows = append(rows, r)
	}
	out["rows"] = rows
	return out
}

func TestContractOps(t *testing.T) {
	cases := loadCases(t, "ops")
	if len(cases) < 5 {
		t.Fatalf("only %d ops cases loaded", len(cases))
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			got := runOps(t, c)
			if !equal(got, generic(c.Expect)) {
				g, _ := json.Marshal(got)
				w, _ := json.Marshal(c.Expect)
				t.Fatalf("got  %s\nwant %s", g, w)
			}
		})
	}
}

// ── 5. HTTP: the legacy era (ruling R6) and the hardening ───────────────────
// opsToken() is the HTTP fixture token: lumen-ops's own scope and audience.
func opsToken() string { return tok(auth.ScopeWrite, auth.OpsResource, "lumen-cli", "user-1") }

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

// bearer adds `Authorization: Bearer <token>` to every request (what the client's -token does).
type bearer struct {
	token string
	next  http.RoundTripper
}

func (b bearer) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	r.Header.Set("Authorization", "Bearer "+b.token)
	return b.next.RoundTrip(r)
}

func transportAs(url, token string) *mcp.StreamableClientTransport {
	return &mcp.StreamableClientTransport{Endpoint: url + "/mcp",
		HTTPClient: &http.Client{Transport: bearer{token, http.DefaultTransport}}}
}

func TestLegacyClientFailsClosedOverStatelessHTTP(t *testing.T) {
	// A 2025-11-25 client is willing to confirm, but a Stateless server has no channel to ask it
	// mid-call. label_ticket fails closed with the fixed decline and writes nothing.
	path := labelsFile(t)
	ts := serveLocal(t, NewOpsServer(path))
	var seen []asked
	client := mcp.NewClient(&mcp.Implementation{Name: "legacy", Version: "0"},
		&mcp.ClientOptions{ElicitationHandler: replying(accept, &seen)})
	cs, err := client.Connect(context.Background(), transportAs(ts.URL, opsToken()),
		&mcp.ClientSessionOptions{ProtocolVersion: "2025-11-25"})
	if err != nil {
		t.Fatal(err)
	}
	defer cs.Close()
	if v := cs.InitializeResult().ProtocolVersion; v != "2025-11-25" {
		t.Fatalf("negotiated %q, want the legacy era", v)
	}
	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "label_ticket",
		Arguments: map[string]any{"id": "t001", "label": "bug"}})
	if err != nil {
		t.Fatalf("CallTool: %v", err)
	}
	wantDeclined(t, res)
	if len(seen) != 0 || len(readRows(t, path)) != 0 {
		t.Errorf("asked %d, rows %d", len(seen), len(readRows(t, path)))
	}
}

func TestModernClientConfirmsOverHTTP(t *testing.T) {
	path := labelsFile(t)
	ts := serveLocal(t, NewOpsServer(path))
	var seen []asked
	cs, err := mcp.NewClient(&mcp.Implementation{Name: "modern", Version: "0"},
		&mcp.ClientOptions{ElicitationHandler: replying(accept, &seen)}).
		Connect(context.Background(), transportAs(ts.URL, opsToken()), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer cs.Close()
	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "label_ticket",
		Arguments: map[string]any{"id": "t002", "label": "bug"}})
	if err != nil || res.IsError {
		t.Fatalf("err %v res %+v", err, res)
	}
	if len(seen) != 1 || len(readRows(t, path)) != 1 {
		t.Errorf("asked %d, rows %d", len(seen), len(readRows(t, path)))
	}
}

const listBody = `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`

func send(h http.Handler, method, body, origin, token string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, "http://127.0.0.1:8766/mcp", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func post(h http.Handler, body, origin string) int {
	return send(h, "POST", body, origin, opsToken()).Code
}

func TestOriginRejected(t *testing.T) {
	h := httpHandler(t, NewOpsServer(labelsFile(t)))
	for origin, want := range map[string]int{
		"http://evil.example":   http.StatusForbidden,
		"http://127.0.0.1:6274": http.StatusOK,
		"http://127.0.0.1:8766": http.StatusOK,
		"":                      http.StatusOK,
	} {
		if got := post(h, listBody, origin); got != want {
			t.Errorf("Origin %q: got %d, want %d", origin, got, want)
		}
	}
}

func TestBodyCap(t *testing.T) {
	h := httpHandler(t, NewOpsServer(labelsFile(t)))
	pad := func(n int) string { return listBody + strings.Repeat(" ", n-len(listBody)) }
	if got := post(h, pad(core.MaxBodyBytes), ""); got != http.StatusOK {
		t.Errorf("exactly the cap: %d", got)
	}
	if got := post(h, pad(core.MaxBodyBytes+1), ""); got != http.StatusRequestEntityTooLarge {
		t.Errorf("over the cap: %d", got)
	}
}

func TestDefaultPort(t *testing.T) {
	if DefaultPort != 8766 {
		t.Errorf("DefaultPort %d", DefaultPort)
	}
}

// ── 6. Authorization (lumen-ops is a resource server) ───────────────────────
func TestNoToken401WithMetadata(t *testing.T) {
	rec := send(httpHandler(t, NewOpsServer(labelsFile(t))), "POST", listBody, "", "")
	want := `resource_metadata="http://127.0.0.1:8766/.well-known/oauth-protected-resource/mcp"`
	if rec.Code != 401 || !strings.Contains(rec.Header().Get("WWW-Authenticate"), want) {
		t.Fatalf("got %d %q", rec.Code, rec.Header().Get("WWW-Authenticate"))
	}
}

func TestRightTokenOK(t *testing.T) {
	if got := post(httpHandler(t, NewOpsServer(labelsFile(t))), listBody, ""); got != 200 {
		t.Fatalf("got %d", got)
	}
}

func TestWrongAudience401(t *testing.T) {
	h := httpHandler(t, NewOpsServer(labelsFile(t)))
	for _, aud := range []string{auth.ReadResource, "http://127.0.0.1:9999/mcp"} {
		if rec := send(h, "POST", listBody, "", tok(auth.ScopeWrite, aud, "lumen-cli", "user-1")); rec.Code != 401 {
			t.Errorf("aud %s: got %d", aud, rec.Code)
		}
	}
}

func TestWrongScope403(t *testing.T) {
	h := httpHandler(t, NewOpsServer(labelsFile(t)))
	for _, scope := range []string{auth.ScopeRead, "", "tickets:admin"} {
		rec := send(h, "POST", listBody, "", tok(scope, auth.OpsResource, "lumen-cli", "user-1"))
		wa := rec.Header().Get("WWW-Authenticate")
		if rec.Code != 403 || !strings.Contains(wa, "resource_metadata=") || !strings.Contains(wa, `error="insufficient_scope"`) {
			t.Errorf("scope %q: got %d %q", scope, rec.Code, wa)
		}
	}
}

func TestCrossServerTokenRejected(t *testing.T) {
	// Review focus 3. The audience is checked while verifying the token (R7) and the scope only on a
	// token that verified, so lumen-read's own token is 401 here (audience), with or without a forged
	// write scope; a token for this audience that lacks tickets:write is 403 (scope).
	h := httpHandler(t, NewOpsServer(labelsFile(t)))
	for name, c := range map[string]struct {
		token string
		want  int
	}{
		"read_token":            {tok(auth.ScopeRead, auth.ReadResource, "lumen-cli", "user-1"), 401},
		"forged_scope_read_aud": {tok(auth.ScopeWrite, auth.ReadResource, "lumen-cli", "user-1"), 401},
		"ops_aud_read_scope":    {tok(auth.ScopeRead, auth.OpsResource, "lumen-cli", "user-1"), 403},
	} {
		if rec := send(h, "POST", listBody, "", c.token); rec.Code != c.want {
			t.Errorf("%s: got %d, want %d", name, rec.Code, c.want)
		}
	}
}

func TestOpsClientIDFromToken(t *testing.T) {
	// The label row's client_id, and the confirmation state's Sub and ClientID, come from the
	// verified token. A state issued to one token is refused when answered with another.
	path := labelsFile(t)
	ts := serveLocal(t, NewOpsServer(path))
	connect := func(token string, manualRounds bool) *mcp.ClientSession {
		var seen []asked
		opts := &mcp.ClientOptions{ElicitationHandler: replying(accept, &seen)}
		if manualRounds {
			opts.MultiRoundTrip = &mcp.MultiRoundTripOptions{Disabled: true}
		}
		cs, err := mcp.NewClient(&mcp.Implementation{Name: "id", Version: "0"}, opts).
			Connect(context.Background(), transportAs(ts.URL, token), nil)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { cs.Close() })
		return cs
	}
	tok7 := tok(auth.ScopeWrite, auth.OpsResource, "lumen-test-client-7", "user-42")
	res, err := connect(tok7, false).CallTool(context.Background(), &mcp.CallToolParams{Name: "label_ticket",
		Arguments: map[string]any{"id": "t001", "label": "billing"}})
	if err != nil || res.IsError {
		t.Fatalf("err %v res %+v", err, res)
	}
	rows := readRows(t, path)
	if len(rows) != 1 || rows[0]["client_id"] != "lumen-test-client-7" {
		t.Fatalf("rows %v", rows)
	}
	// The state binds the token's subject and client.
	cs7 := connect(tok7, true)
	state := round1(t, cs7, "t002", "bug")
	payload, _, _ := strings.Cut(state, ".")
	raw, _ := base64.RawURLEncoding.DecodeString(payload)
	var st confirmState
	if err := json.Unmarshal(raw, &st); err != nil || st.Sub != "user-42" || st.ClientID != "lumen-test-client-7" {
		t.Fatalf("state %+v %v", st, err)
	}
	other := connect(tok(auth.ScopeWrite, auth.OpsResource, "someone-else", "user-43"), true)
	wantDeclined(t, round(t, other, "t002", "bug", accept, state))
	if rows := readRows(t, path); len(rows) != 1 {
		t.Fatalf("another caller's answer wrote: %d rows", len(rows))
	}
}

func TestGetWithBadOrigin403(t *testing.T) {
	// R18: Origin is checked for every method (CrossOriginProtection alone passes GET). With a valid token.
	h := httpHandler(t, NewOpsServer(labelsFile(t)))
	if rec := send(h, "GET", "", "http://evil.example", opsToken()); rec.Code != 403 {
		t.Errorf("GET bad Origin: %d", rec.Code)
	}
	if rec := send(h, "GET", "", "http://127.0.0.1:6274", opsToken()); rec.Code == 403 {
		t.Errorf("GET Inspector Origin: 403")
	}
	if rec := send(h, "POST", listBody, "http://evil.example", ""); rec.Code != 403 {
		t.Errorf("R42 ordering: Origin is checked before auth, want 403 without a token, got %d", rec.Code)
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
	ts := serveLocal(t, NewOpsServer(labelsFile(t)))
	port := ts.Listener.Addr().(*net.TCPAddr).Port
	token := opsToken()
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
