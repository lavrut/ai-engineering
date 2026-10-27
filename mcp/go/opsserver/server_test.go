package main

// Test transport: the in-memory transport (mcp.NewInMemoryTransports) runs the server and a real Go
// client in-process for the contract and the confirmation tests. The legacy-era test and the HTTP
// hardening tests use the real Stateless HTTP handler (httptest), because that is where ruling R6
// and the Origin/body-cap checks live. Every test writes to a t.TempDir() labels file.

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

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

func TestSpentStatesExpire(t *testing.T) {
	clock := time.Unix(1000, 0)
	s := newSpentStates(StateTTL, func() time.Time { return clock })
	if StateTTL != 5*time.Minute || !s.spend("a") || s.spend("a") {
		t.Fatal("first spend must succeed, the replay must not")
	}
	clock = clock.Add(StateTTL + time.Second) // by now verifyState rejects "a" as expired anyway
	if !s.spend("b") || s.size() != 1 {
		t.Fatalf("expired entries must be pruned: size %d", s.size())
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
func TestLegacyClientFailsClosedOverStatelessHTTP(t *testing.T) {
	// A 2025-11-25 client is willing to confirm, but a Stateless server has no channel to ask it
	// mid-call. label_ticket fails closed with the fixed decline and writes nothing.
	path := labelsFile(t)
	ts := httptest.NewServer(NewHTTPHandler(NewOpsServer(path)))
	defer ts.Close()
	var seen []asked
	client := mcp.NewClient(&mcp.Implementation{Name: "legacy", Version: "0"},
		&mcp.ClientOptions{ElicitationHandler: replying(accept, &seen)})
	cs, err := client.Connect(context.Background(), &mcp.StreamableClientTransport{Endpoint: ts.URL + "/mcp"},
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
	ts := httptest.NewServer(NewHTTPHandler(NewOpsServer(path)))
	defer ts.Close()
	var seen []asked
	cs, err := mcp.NewClient(&mcp.Implementation{Name: "modern", Version: "0"},
		&mcp.ClientOptions{ElicitationHandler: replying(accept, &seen)}).
		Connect(context.Background(), &mcp.StreamableClientTransport{Endpoint: ts.URL + "/mcp"}, nil)
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

func post(h http.Handler, body, origin string) int {
	req := httptest.NewRequest("POST", "http://127.0.0.1:8766/mcp", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec.Code
}

func TestOriginRejected(t *testing.T) {
	h := NewHTTPHandler(NewOpsServer(labelsFile(t)))
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
	h := NewHTTPHandler(NewOpsServer(labelsFile(t)))
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
