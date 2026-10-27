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
// HTTP only by design (a write server is not a subprocess any host can spawn), and always as an OAuth
// resource server (scope tickets:write, audience http://127.0.0.1:8766/mcp; see mcp/go/auth). The
// label row's client_id is the verified token's client_id. Tests use the in-memory transport
// against NewOpsServer, which has no HTTP and so no bearer check.
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

	"lumen-mcp/auth"
	"lumen-mcp/core"
)

// ── 1. Configuration and helpers ────────────────────────────────────────────
const Instructions = "Lumen ticket labelling. label_ticket writes a label only after the user confirms it; " +
	"a declined or cancelled confirmation writes nothing."

const (
	DefaultPort = 8766
	StateTTL    = 5 * time.Minute  // a confirmation round must finish within five minutes
	PruneMargin = 60 * time.Second // a spent state is forgotten only this long after its own expiry
	anonymous   = "anonymous"      // client_id when no token identifies the caller (in-memory tests only)
	confirmKey  = "confirm"        // the input request's id in InputRequests / InputResponses
	modernEra   = "2026-07-28"     // the first protocol version with input_required results
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

// spentStates holds the MAC of every state that has been answered, each until that state's own
// expiry plus PruneMargin, on the same clock verify uses: by the time an entry goes, verify already
// rejects the state as expired. A valid signature alone would let the same accepted retry write
// twice; this makes a state single-use. In memory, one process: a multi-instance deployment would
// need a shared store and key.
type spentStates struct {
	mu   sync.Mutex
	now  func() time.Time
	seen map[string]time.Time // MAC -> the state's own expiry
}

func newSpentStates(now func() time.Time) *spentStates {
	return &spentStates{now: now, seen: map[string]time.Time{}}
}

// spend is true the first time a MAC is spent and false for every replay while it is remembered.
func (s *spentStates) spend(mac string, expires time.Time) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	for k, exp := range s.seen {
		if now.After(exp.Add(PruneMargin)) {
			delete(s.seen, k)
		}
	}
	if _, dup := s.seen[mac]; dup {
		return false
	}
	s.seen[mac] = expires
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
	o.spent = newSpentStates(func() time.Time { return o.now() })
	return o
}

func (o *ops) sign(s confirmState) string {
	payload, _ := json.Marshal(s)
	m := hmac.New(sha256.New, o.key)
	m.Write(payload)
	return base64.RawURLEncoding.EncodeToString(payload) + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

// verify returns the state's MAC and expiry when it is ours, unmodified, for this caller and these
// arguments, and younger than StateTTL; "" for anything else.
func (o *ops) verify(raw string, want confirmState) (string, time.Time) {
	p64, s64, found := strings.Cut(raw, ".")
	if !found {
		return "", time.Time{}
	}
	payload, err1 := base64.RawURLEncoding.DecodeString(p64)
	sig, err2 := base64.RawURLEncoding.DecodeString(s64)
	if err1 != nil || err2 != nil {
		return "", time.Time{}
	}
	m := hmac.New(sha256.New, o.key)
	m.Write(payload)
	if !hmac.Equal(sig, m.Sum(nil)) {
		return "", time.Time{}
	}
	var got confirmState
	if json.Unmarshal(payload, &got) != nil {
		return "", time.Time{}
	}
	issued := time.Unix(got.Iat, 0)
	age := o.now().Sub(issued)
	if got.Sub != want.Sub || got.ClientID != want.ClientID || got.ID != want.ID || got.Label != want.Label ||
		age < 0 || age > StateTTL {
		return "", time.Time{}
	}
	return s64, issued.Add(StateTTL)
}

// caller is who is asking: the verified token's subject (TokenInfo.UserID, the `sub` claim) and
// client (TokenInfo.Extra["client_id"]), both filled by auth.Verifier. Anonymous only in-memory.
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
		mac, expires := o.verify(req.Params.RequestState, want)
		if mac == "" || !o.spent.spend(mac, expires) {
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

// NewHTTPHandler is the Streamable HTTP handler: /mcp (Stateless) as an OAuth resource server for
// auth.OpsResource with scope auth.ScopeWrite, and the protected-resource metadata at auth.PRMPath. The
// order of checks (Origin for every method -> body cap -> bearer token) is in auth.Handler.
func NewHTTPHandler(s *mcp.Server) http.Handler {
	mcpHandler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return s },
		&mcp.StreamableHTTPOptions{Stateless: true, MaxRequestBodyBytes: core.MaxBodyBytes})
	return auth.Handler(mcpHandler, auth.OpsResource, auth.ScopeWrite)
}
