// Command opsserver is lumen-ops: the Lumen write server. It labels a ticket, only after the user
// confirms (Go).
//
// One tool, label_ticket(id, label). It validates first, then asks the user one yes/no question
// through a form elicitation, and appends one label row with provenance only on an accepted "yes".
// The Python server (mcp/python/ops_server.py) answers the same shared contract,
// mcp/fixtures/contract.jsonl, and the numbered sections below match it.
//
// What the confirmation proves: only that the retry came from the same caller, for the same
// arguments, within five minutes, on a state this server signed and has not seen answered before.
// Whether a human or a script said "yes" is the client's claim; confirmed=true records the claim.
//
// HTTP only by design (a write server is not a subprocess any host can spawn). Tests use the
// in-memory transport against NewOpsServer.
package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/google/jsonschema-go/jsonschema"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"lumen-mcp/core"
)

// ── 1. Configuration and helpers ────────────────────────────────────────────
const Instructions = "Lumen ticket labelling. label_ticket writes a label only after the user confirms it; " +
	"a declined or cancelled confirmation writes nothing."

const (
	DefaultPort = 8766
	StateTTL    = 5 * time.Minute // a confirmation round must finish within five minutes
	anonymous   = "anonymous"     // client_id when no token identifies the caller (auth arrives in Task 8)
	confirmKey  = "confirm"       // the input request's id in InputRequests / InputResponses
	modernEra   = "2026-07-28"    // the first protocol version with input_required results
)

// The fixed error texts. Identical in the Python server; the contract pins them.
var ErrLabel = "label must be one of: " + strings.Join(core.Categories, ", ")

const Declined = "label not written: the user declined"

func errUnknown(id string) string { return "unknown ticket: " + id }

// fail is a tool error the model can read: isError true with exactly the fixed text.
func fail(message string) *mcp.CallToolResult {
	return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: message}}}
}

// marshal is json.Marshal without HTML escaping, so the text block reads like the Python one.
func marshal(v any) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "", err
	}
	return string(bytes.TrimRight(buf.Bytes(), "\n")), nil
}

// ok is structured content plus a JSON text block (the same JSON) for clients that read only text.
func ok(out any) (*mcp.CallToolResult, any, error) {
	text, err := marshal(out)
	if err != nil {
		return nil, nil, err
	}
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: text}}}, out, nil
}

type LabelIn struct {
	ID    string `json:"id" jsonschema:"ticket id, for example t001"`
	Label string `json:"label" jsonschema:"one of billing, bug, account, how_to, feature_request, outage"`
}
type LabelOut struct {
	TicketID string `json:"ticket_id"`
	Label    string `json:"label"`
	Written  bool   `json:"written"`
}

// validationError checks the label first, then the ticket, before anything is put to the user.
func validationError(in LabelIn) string {
	if !slices.Contains(core.Categories, in.Label) {
		return ErrLabel
	}
	if _, err := core.Get(in.ID); err != nil {
		return errUnknown(in.ID)
	}
	return ""
}

// ── 2. The confirmation question ────────────────────────────────────────────
// confirmSchema is the same map the Python SDK sends for `class Confirm(BaseModel): confirm: bool`.
var confirmSchema = map[string]any{
	"type":       "object",
	"properties": map[string]any{"confirm": map[string]any{"type": "boolean", "title": "Confirm"}},
	"required":   []string{"confirm"},
}

func question(in LabelIn) *mcp.ElicitParams {
	return &mcp.ElicitParams{Mode: "form", Message: fmt.Sprintf("Set ticket %s to %q?", in.ID, in.Label),
		RequestedSchema: confirmSchema}
}

// ── 3. Signed, single-use confirmation states ───────────────────────────────
// The Go SDK leaves RequestState to the server ("encrypt, sign and verify"). It is HMAC-SHA256
// over who asked (sub, client), what for (id, label), when (iat) and a random nonce, so two
// confirmations of the same label get different states. A client can read it but not forge it.
type confirmState struct {
	Sub, ClientID, ID, Label string
	Iat                      int64
	Nonce                    string
}

// spentStates holds the MAC of every state that has been answered, each for one TTL. A valid
// signature alone would let the same accepted retry write twice; this makes a state single-use.
// In memory, one process: a multi-instance deployment would need a shared store and key.
type spentStates struct {
	mu   sync.Mutex
	ttl  time.Duration
	now  func() time.Time
	seen map[string]time.Time
}

func newSpentStates(ttl time.Duration, now func() time.Time) *spentStates {
	return &spentStates{ttl: ttl, now: now, seen: map[string]time.Time{}}
}

// spend is true the first time a MAC is spent and false for every replay within the TTL.
func (s *spentStates) spend(mac string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	for k, t := range s.seen {
		if now.Sub(t) > s.ttl {
			delete(s.seen, k)
		}
	}
	if _, dup := s.seen[mac]; dup {
		return false
	}
	s.seen[mac] = now
	return true
}

func (s *spentStates) size() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.seen)
}

// ops is one lumen-ops server's private state: its signing key (random per process, so a
// restart invalidates open confirmations), its clock and its spent states.
type ops struct {
	labelsPath string
	key        []byte
	now        func() time.Time
	spent      *spentStates
}

func newOps(labelsPath string) *ops {
	if labelsPath == "" {
		labelsPath = core.LabelsPath()
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		panic(err)
	}
	o := &ops{labelsPath: labelsPath, key: key, now: time.Now}
	o.spent = newSpentStates(StateTTL, func() time.Time { return o.now() })
	return o
}

func (o *ops) sign(s confirmState) string {
	payload, _ := json.Marshal(s)
	m := hmac.New(sha256.New, o.key)
	m.Write(payload)
	return base64.RawURLEncoding.EncodeToString(payload) + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

// verify returns the state's MAC when it is ours, unmodified, for this caller and these
// arguments, and younger than StateTTL; "" for anything else.
func (o *ops) verify(raw string, want confirmState) string {
	p64, s64, found := strings.Cut(raw, ".")
	if !found {
		return ""
	}
	payload, err1 := base64.RawURLEncoding.DecodeString(p64)
	sig, err2 := base64.RawURLEncoding.DecodeString(s64)
	if err1 != nil || err2 != nil {
		return ""
	}
	m := hmac.New(sha256.New, o.key)
	m.Write(payload)
	if !hmac.Equal(sig, m.Sum(nil)) {
		return ""
	}
	var got confirmState
	if json.Unmarshal(payload, &got) != nil {
		return ""
	}
	age := o.now().Sub(time.Unix(got.Iat, 0))
	if got.Sub != want.Sub || got.ClientID != want.ClientID || got.ID != want.ID || got.Label != want.Label ||
		age < 0 || age > StateTTL {
		return ""
	}
	return s64
}

// caller is who is asking: the token's subject and client, or anonymous without auth.
func caller(req *mcp.CallToolRequest) (sub, clientID string) {
	clientID = anonymous
	if req.Extra != nil && req.Extra.TokenInfo != nil {
		ti := req.Extra.TokenInfo
		sub = ti.UserID
		if c, _ := ti.Extra["client_id"].(string); c != "" {
			clientID = c
		}
	}
	return sub, clientID
}

// modernClient reports whether the client speaks 2026-07-28 (input_required results). As in the
// SDK, a session with no initialize params is taken to be on the latest version.
func modernClient(req *mcp.CallToolRequest) bool {
	ip := req.Session.InitializeParams()
	return ip == nil || ip.ProtocolVersion >= modernEra
}

// NewOpsServer builds the lumen-ops server; labelsPath "" means core.LabelsPath().
func NewOpsServer(labelsPath string) *mcp.Server { return newOps(labelsPath).server() }

func (o *ops) server() *mcp.Server {
	s := mcp.NewServer(&mcp.Implementation{Name: "lumen-ops", Version: "0.1.0"}, &mcp.ServerOptions{Instructions: Instructions})
	notDestructive := false
	schema, err := jsonschema.For[LabelOut](nil)
	if err != nil {
		panic(err)
	}

	// ── 4. Tools ────────────────────────────────────────────────────────────
	mcp.AddTool(s, &mcp.Tool{
		Name:         "label_ticket",
		Description:  "Label a ticket with one of the six categories. Asks the user to confirm; writes only on yes.",
		Annotations:  &mcp.ToolAnnotations{ReadOnlyHint: false, DestructiveHint: &notDestructive},
		OutputSchema: schema,
	}, func(ctx context.Context, req *mcp.CallToolRequest, in LabelIn) (*mcp.CallToolResult, any, error) {
		if msg := validationError(in); msg != "" { // checked first, so the fixed text wins and nobody is asked
			return fail(msg), nil, nil
		}
		// Ruling R6. A legacy-era (2025-11-25) client gets questions as server-to-client requests in
		// the middle of a call, and this server runs Stateless: there is no open channel to send one
		// on (the SDK would fail the call with a JSON-RPC error). So label_ticket fails closed for
		// them up front: no question, nothing written, the fixed decline. Legacy hosts can still
		// list the tool; they cannot confirm a write here. (Python, with sessions, can.)
		if !modernClient(req) {
			return fail(Declined), nil, nil
		}
		sub, clientID := caller(req)
		want := confirmState{Sub: sub, ClientID: clientID, ID: in.ID, Label: in.Label}
		resp, answered := req.Params.InputResponses[confirmKey]
		if !answered { // round 1: ask, with a signed state to echo back
			nonce := make([]byte, 16)
			if _, err := rand.Read(nonce); err != nil {
				return nil, nil, err
			}
			want.Iat, want.Nonce = o.now().Unix(), base64.RawURLEncoding.EncodeToString(nonce)
			return &mcp.CallToolResult{InputRequests: mcp.InputRequestMap{confirmKey: question(in)},
				RequestState: o.sign(want)}, nil, nil
		}
		// Round 2: fail closed on any bad, foreign, stale or already-answered state.
		mac := o.verify(req.Params.RequestState, want)
		if mac == "" || !o.spent.spend(mac) {
			return fail(Declined), nil, nil
		}
		er, _ := resp.(*mcp.ElicitResult)
		if er == nil || er.Action != "accept" || er.Content["confirm"] != true { // only the boolean true
			return fail(Declined), nil, nil
		}
		if _, err := core.WriteLabel(in.ID, in.Label, core.LabelInput{Path: o.labelsPath, Source: "agent",
			ClientID: clientID, Model: nil, Confirmed: true}); err != nil {
			return nil, nil, err
		}
		return ok(LabelOut{TicketID: in.ID, Label: in.Label, Written: true})
	})

	return s
}

// ── 5. HTTP handler and security ────────────────────────────────────────────
// capBody answers 413 for a body over the cap, by Content-Length first and then while reading
// (MaxBytesReader also bounds chunked bodies).
func capBody(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ContentLength > core.MaxBodyBytes {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, core.MaxBodyBytes)
		next.ServeHTTP(w, r)
	})
}

// TrustedOrigins are the only browser origins allowed besides the server's own: the Inspector web UI.
var TrustedOrigins = []string{"http://127.0.0.1:6274", "http://localhost:6274"}

// NewHTTPHandler is the Streamable HTTP handler for /mcp: Stateless, Origin check first (403), then
// the body cap (413). No auth yet: Task 8 adds it.
func NewHTTPHandler(s *mcp.Server) http.Handler {
	mcpHandler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return s },
		&mcp.StreamableHTTPOptions{Stateless: true, MaxRequestBodyBytes: core.MaxBodyBytes})
	cop := http.NewCrossOriginProtection()
	for _, o := range TrustedOrigins {
		if err := cop.AddTrustedOrigin(o); err != nil {
			panic(err)
		}
	}
	mux := http.NewServeMux()
	mux.Handle("/mcp", cop.Handler(capBody(mcpHandler)))
	return mux
}
