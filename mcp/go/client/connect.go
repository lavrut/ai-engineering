// Lumen MCP client, part 2 of 3: connecting over stdio or Streamable HTTP (section 1). The Python
// twin is mcp/python/client_connect.py. main.go has the configuration, discovery and the entry point.

package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	neturl "net/url"
	"os"
	"strings"
	"sync"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// ── 1. Connecting (stdio / HTTP) ────────────────────────────────────────────

// bearer sets `Authorization: Bearer <token>` only on requests to the endpoint's own origin (same
// scheme and host:port). A token is good for one server; it must never reach anyone else.
type bearer struct {
	token  string
	origin *neturl.URL // the endpoint: only Scheme and Host are compared
	next   http.RoundTripper
}

func (b bearer) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.URL.Scheme != b.origin.Scheme || r.URL.Host != b.origin.Host {
		return b.next.RoundTrip(r) // another origin: no credential
	}
	r = r.Clone(r.Context()) // a RoundTripper must not modify the caller's request
	r.Header.Set("Authorization", "Bearer "+b.token)
	return b.next.RoundTrip(r)
}

// refusal remembers the last non-2xx answer the server gave to a POST (an MCP message), so a failed
// call can say why in one line: refusalLine. The Python client prints the same line.
type refusal struct {
	mu     sync.Mutex
	status int
}

func (r *refusal) get() int {
	if r == nil {
		return 0
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.status
}

func refusalLine(status int) string {
	return fmt.Sprintf("client: the server refused the request: HTTP %d %s", status, http.StatusText(status))
}

// recordRefusals is the RoundTripper that fills a refusal.
type recordRefusals struct {
	rec  *refusal
	next http.RoundTripper
}

func (t recordRefusals) RoundTrip(r *http.Request) (*http.Response, error) {
	resp, err := t.next.RoundTrip(r)
	if err == nil && r.Method == http.MethodPost && resp.StatusCode >= 300 {
		t.rec.mu.Lock()
		t.rec.status = resp.StatusCode
		t.rec.mu.Unlock()
	}
	return resp, err
}

// noRedirects makes the HTTP client return a redirect as it is instead of following it (the
// Python SDK follows one only within the endpoint's own origin; lesson 7 notes the difference). A 3xx
// then fails the MCP request, and the token is never replayed to the redirect's target.
func noRedirects(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }

// tokenFlagWarning is printed (to stderr) whenever -token is given. The Python client prints the same line.
const tokenFlagWarning = "client: warning: a token on the command line is visible in the process list and shell " +
	"history; use a token file or LUMEN_TOKEN"

// plainHTTPWarning is printed (to stderr) before a token goes to a plain-http URL off this machine.
// The Python client prints the same line.
const plainHTTPWarning = "client: warning: the token goes over plain http to a host that is not this machine, " +
	"so anyone on the network path can read it; use https"

// plainHTTPOffMachine is true for an http:// URL whose host is not 127.0.0.1, localhost or ::1.
func plainHTTPOffMachine(raw string) bool {
	u, err := neturl.Parse(raw)
	if err != nil || u.Scheme != "http" {
		return false
	}
	h := strings.ToLower(u.Hostname())
	return h != "127.0.0.1" && h != "localhost" && h != "::1"
}

// resolveToken picks the token to send and whether to warn (a token was given on the command line).
// Precedence: -token-file, then -token, then LUMEN_TOKEN. Empty values count as absent.
func resolveToken(tokenFile, token, env string) (string, bool, error) {
	warn := token != ""
	if tokenFile != "" {
		raw, err := os.ReadFile(tokenFile)
		if err != nil {
			return "", warn, fmt.Errorf("token file not readable: %s", tokenFile)
		}
		first, _, _ := strings.Cut(strings.TrimSpace(string(raw)), "\n")
		if first = strings.TrimSpace(first); first == "" {
			return "", warn, fmt.Errorf("token file is empty: %s", tokenFile)
		}
		return first, warn, nil
	}
	if token != "" {
		return token, warn, nil
	}
	return env, warn, nil
}

// newTransport is the one place the transport is built. With a token, the HTTP client sends
// `Authorization: Bearer <token>` on requests to the endpoint's origin only; the URL is left exactly
// as given (a token never goes in it). Redirects are never followed. rec (may be nil) records a
// refusal. A token with -stdio is refused: it is an HTTP credential.
func newTransport(stdio bool, url, token string, rec *refusal) (mcp.Transport, error) {
	if stdio {
		if token != "" {
			return nil, errors.New("a token needs --url / -url")
		}
		cmd, err := stdioCommand()
		if err != nil {
			return nil, err
		}
		return &mcp.CommandTransport{Command: cmd}, nil
	}
	origin, err := neturl.Parse(url)
	if err != nil || origin.Host == "" {
		return nil, fmt.Errorf("not an http(s) URL: %s", url)
	}
	var rt http.RoundTripper = http.DefaultTransport
	if token != "" {
		rt = bearer{token, origin, rt}
	}
	if rec != nil {
		rt = recordRefusals{rec, rt}
	}
	return &mcp.StreamableClientTransport{Endpoint: url,
		HTTPClient: &http.Client{Transport: rt, CheckRedirect: noRedirects}}, nil
}

// connect opens a session. Setting ElicitationHandler is what declares the elicitation capability,
// so a server's confirmation step only runs when the client can answer it.
func connect(ctx context.Context, transport mcp.Transport, answer Answerer) (*mcp.ClientSession, error) {
	client := mcp.NewClient(&mcp.Implementation{Name: "lumen-client", Version: "0.1.0"}, &mcp.ClientOptions{
		ElicitationHandler: elicitationHandler(answer),
	})
	return client.Connect(ctx, transport, nil)
}
