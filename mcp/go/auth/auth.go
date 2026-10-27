// Package auth is the resource-server half of OAuth for lumen-read (HTTP) and lumen-ops (Go), plus
// the HTTP guards both servers share.
//
// Both servers are OAuth 2.1 resource servers: they never issue tokens, they only check them. A
// token is accepted when it is signed by the test AS (mcp/python/auth_server.py), unexpired, from
// the expected issuer, and its audience (RFC 8707) names this server's canonical URI. Then the SDK
// middleware checks the scope (403 if missing). A missing or rejected token is 401 with
// `WWW-Authenticate: Bearer resource_metadata="..."`. No token is ever passed on to anything else.
//
// The audience check lives in the verifier (ruling R7): the Go SDK's RequireBearerToken checks
// scopes and expiry but not audience. Order (ruling R24): the verifier runs first and rejects a
// token for another server with 401; only a verified token reaches the scope check (403). Audience
// before scope, as the spec says, so lumen-read's token at lumen-ops is 401, not 403. The Python
// servers do the same in mcp/python/resource_auth.py, and the numbered sections match it.
//
// The signing key is never in the source (ruling R25): the test AS writes 32 random bytes (hex,
// mode 0600, git-ignored) to mcp/data/dev-signing.key on first start, and LoadSigningKey reads it
// (or the file named by LUMEN_SIGNING_KEY_FILE). A server without it refuses to start.
package auth

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	sdkauth "github.com/modelcontextprotocol/go-sdk/auth"
	"github.com/modelcontextprotocol/go-sdk/oauthex"

	"lumen-mcp/core"
)

// ── 1. Configuration ────────────────────────────────────────────────────────
const (
	Issuer       = "http://127.0.0.1:9000"     // the local test AS
	ReadResource = "http://127.0.0.1:8765/mcp" // lumen-read's canonical URI (the token audience)
	OpsResource  = "http://127.0.0.1:8766/mcp" // lumen-ops's canonical URI
	ScopeRead    = "tickets:read"
	ScopeWrite   = "tickets:write"
	PRMPath      = "/.well-known/oauth-protected-resource/mcp" // RFC 9728, path-aware for /mcp
	Leeway       = 30 * time.Second                            // clock skew allowed on nbf / iat (never on exp: R29)
)

const (
	KeyFileEnv  = "LUMEN_SIGNING_KEY_FILE"
	keyFileName = "dev-signing.key" // in mcp/data, next to labels.jsonl
	keyBytes    = 32
)

// KeyFile is the signing key file: $LUMEN_SIGNING_KEY_FILE, else mcp/data/dev-signing.key.
func KeyFile() string {
	if p := os.Getenv(KeyFileEnv); p != "" {
		return p
	}
	return filepath.Join(core.Root(), "mcp", "data", keyFileName)
}

// LoadSigningKey reads the HS256 key the test AS signs with: 32 bytes stored as one line of hex,
// readable by the owner only. There is no default key: without the file it returns an error that
// says how to fix it. HS256 is symmetric, so whoever holds this file can mint tokens; acceptable
// only on a loopback lab. Production uses an asymmetric key (RS256/ES256) published by the AS as a
// JWKS, so resource servers hold no secret. The Python twin is resource_auth.load_signing_key.
func LoadSigningKey() ([]byte, error) {
	path := KeyFile()
	fix := "start the test AS first (python mcp/python/auth_server.py), or set " + KeyFileEnv
	info, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("signing key file %s not found or unreadable: %s", path, fix)
	}
	if info.Mode().Perm()&0o077 != 0 {
		return nil, fmt.Errorf("signing key file %s is readable by others: chmod 600 %s", path, path)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("signing key file %s not found or unreadable: %s", path, fix)
	}
	key, err := hex.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil || len(key) != keyBytes {
		return nil, fmt.Errorf("signing key file %s must hold %d bytes as hex: %s", path, keyBytes, fix)
	}
	return key, nil
}

// ErrNoKey is what Verifier panics with when given an empty key: it never verifies without one.
var ErrNoKey = errors.New("empty signing key")

// InspectorOrigins are the only browser origins trusted besides the server's own two local ones
// (rulings R11, R42).
var InspectorOrigins = []string{"http://127.0.0.1:6274", "http://localhost:6274"}

// ResourceURI is a server's canonical resource URI (its token audience, and the `resource` of its
// protected resource metadata) for the port it listens on (ruling R46). ReadResource and OpsResource
// are this for the two default ports.
func ResourceURI(port int) string { return fmt.Sprintf("http://127.0.0.1:%d/mcp", port) }

// ── 2. Token verifier ───────────────────────────────────────────────────────
func b64json(seg string, v any) error {
	b, err := base64.RawURLEncoding.DecodeString(seg)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

// audience reads `aud`: RFC 7519 allows a string or an array of strings. We accept a string or a
// one-entry array (ruling R30): a token for several resources is one any of them could replay to
// the others.
func audience(raw json.RawMessage) (string, bool) {
	var one string
	if json.Unmarshal(raw, &one) == nil && one != "" {
		return one, true
	}
	var many []string
	if json.Unmarshal(raw, &many) == nil && len(many) == 1 && many[0] != "" {
		return many[0], true
	}
	return "", false
}

// Verifier checks an HS256 access token from the test AS for `resource`, signed with `key`:
// signature, issuer, exp (strict, no leeway: R29), nbf/iat (with Leeway: an AS clock slightly ahead
// must not make a fresh token unusable), audience, and that it names a subject and a client.
// Any failure wraps ErrInvalidToken, so RequireBearerToken answers 401. On success, TokenInfo.UserID
// is the token's `sub` and Extra["client_id"] its `client_id`: the only identity the tools trust.
func Verifier(resource string, key []byte) sdkauth.TokenVerifier {
	if len(key) == 0 {
		panic(ErrNoKey) // a programming error: callers load the key with LoadSigningKey first
	}
	return func(_ context.Context, token string, _ *http.Request) (*sdkauth.TokenInfo, error) {
		invalid := func(why string) (*sdkauth.TokenInfo, error) {
			return nil, fmt.Errorf("%w: %s", sdkauth.ErrInvalidToken, why)
		}
		p := strings.Split(token, ".")
		if len(p) != 3 {
			return invalid("malformed")
		}
		var hdr struct {
			Alg string `json:"alg"`
		}
		if b64json(p[0], &hdr) != nil || hdr.Alg != "HS256" { // never "none", never another algorithm
			return invalid("algorithm")
		}
		mac := hmac.New(sha256.New, key)
		mac.Write([]byte(p[0] + "." + p[1]))
		sig, err := base64.RawURLEncoding.DecodeString(p[2])
		if err != nil || !hmac.Equal(sig, mac.Sum(nil)) {
			return invalid("signature")
		}
		var c struct {
			Iss      string          `json:"iss"`
			Aud      json.RawMessage `json:"aud"`
			Sub      string          `json:"sub"`
			ClientID string          `json:"client_id"`
			Scope    string          `json:"scope"`
			Exp      int64           `json:"exp"`
			Nbf      int64           `json:"nbf"`
			Iat      int64           `json:"iat"`
		}
		if b64json(p[1], &c) != nil || c.Iss != Issuer || c.Sub == "" || c.ClientID == "" {
			return invalid("claims")
		}
		now, skew := time.Now().Unix(), int64(Leeway/time.Second)
		// Checked here, not left to the middleware: it checks scope before expiry, which would make
		// an expired token with the wrong scope a 403 instead of a 401. exp is strict (as PyJWT with
		// leeway 0: valid while now < exp); nbf and iat get the skew.
		if c.Exp == 0 || now >= c.Exp || (c.Nbf != 0 && now+skew < c.Nbf) || (c.Iat != 0 && now+skew < c.Iat) {
			return invalid("expired or not yet valid")
		}
		// R7 + R24: the audience is checked here, while verifying, so a token for another server is
		// 401 before RequireBearerToken ever looks at its scopes (the 403 check comes after).
		// R31: Go compares exactly except for a trailing slash (oauthex.MatchesResource); the Python
		// SDK compares URL-normalised. Both accept our canonical URIs and nothing else of note.
		aud, ok := audience(c.Aud)
		if !ok || !oauthex.MatchesResource([]string{aud}, resource) {
			return invalid(fmt.Sprintf("audience %q is not %s", aud, resource))
		}
		return &sdkauth.TokenInfo{Scopes: strings.Fields(c.Scope), Expiration: time.Unix(c.Exp, 0), UserID: c.Sub,
			Extra: map[string]any{"client_id": c.ClientID}}, nil
	}
}

// ── 3. Resource-server handler ──────────────────────────────────────────────

// LocalHosts are the Host values a request to a server on `port` may carry (ruling R42).
func LocalHosts(port int) []string {
	return []string{fmt.Sprintf("127.0.0.1:%d", port), fmt.Sprintf("localhost:%d", port)}
}

// LocalOrigins are the browser origins a server on `port` trusts: its own two local ones and the
// Inspector's two (ruling R42).
func LocalOrigins(port int) []string {
	return append([]string{fmt.Sprintf("http://127.0.0.1:%d", port), fmt.Sprintf("http://localhost:%d", port)},
		InspectorOrigins...)
}

// CheckLocal answers 403 unless the Host header is one of `hosts` and the Origin header is absent (a
// non-browser client) or one of `origins`, for every method (ruling R18: http.CrossOriginProtection
// only gates unsafe methods, and a GET on /mcp opens a stream). Both are checked against explicit
// lists, never against each other (ruling R42): a DNS-rebinding page sends its own name as Host and
// as Origin, so "Origin == Host" would let it through.
func CheckLocal(hosts, origins []string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !slices.Contains(hosts, r.Host) {
			http.Error(w, "forbidden host", http.StatusForbidden)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && !slices.Contains(origins, origin) {
			http.Error(w, "forbidden origin", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// CapBody answers 413 for a body over core.MaxBodyBytes, by Content-Length first and then while
// reading (MaxBytesReader also bounds chunked bodies).
func CapBody(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ContentLength > core.MaxBodyBytes {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, core.MaxBodyBytes)
		next.ServeHTTP(w, r)
	})
}

// withErrorCodes adds the RFC 6750 `error` parameter to the WWW-Authenticate header that the SDK's
// RequireBearerToken writes (it names only resource_metadata and scope): insufficient_scope on a
// 403, invalid_token on a 401 for a request that did present a token. A request with no token
// gets no error code (RFC 6750 §3.1). Only the middleware's own rejections pass through the
// wrapper: the MCP handler is called with the original ResponseWriter, so streaming is untouched.
func withErrorCodes(protect func(http.Handler) http.Handler, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		inner := http.HandlerFunc(func(_ http.ResponseWriter, r2 *http.Request) { next.ServeHTTP(w, r2) })
		protect(inner).ServeHTTP(&errorCodeWriter{ResponseWriter: w, presented: r.Header.Get("Authorization") != ""}, r)
	})
}

type errorCodeWriter struct {
	http.ResponseWriter
	presented bool
}

func (e *errorCodeWriter) WriteHeader(code int) {
	h := e.Header()
	if v := h.Get("WWW-Authenticate"); strings.HasPrefix(v, "Bearer ") {
		switch {
		case code == http.StatusForbidden:
			h.Set("WWW-Authenticate", `Bearer error="insufficient_scope", `+strings.TrimPrefix(v, "Bearer "))
		case code == http.StatusUnauthorized && e.presented:
			h.Set("WWW-Authenticate", `Bearer error="invalid_token", `+strings.TrimPrefix(v, "Bearer "))
		}
	}
	e.ResponseWriter.WriteHeader(code)
}

// MetadataURL is where `resource` serves its protected-resource metadata.
func MetadataURL(resource string) string {
	u, err := url.Parse(resource)
	if err != nil {
		panic(err)
	}
	return u.Scheme + "://" + u.Host + PRMPath
}

// Handler serves `mcpHandler` at /mcp as a resource server for `resource` (tokens signed with
// `key`), listening on `port`, and its metadata at PRMPath. Order on /mcp: Host and Origin against
// explicit lists (403, every method, ruling R42) -> cross-origin protection (403, unsafe methods,
// incl. Sec-Fetch-Site) -> body cap (413) -> bearer token -> MCP. So a bad Host, a bad Origin or an
// oversized body is refused before any token is looked at; the Python servers use the same order.
// Within the bearer step: the verifier (signature, time, audience: 401) runs before the scope check
// (403), ruling R24.
func Handler(mcpHandler http.Handler, resource, scope string, key []byte, port int) http.Handler {
	cop := http.NewCrossOriginProtection()
	for _, o := range LocalOrigins(port) { // CheckLocal already allowed these; the SDK guard must agree
		if err := cop.AddTrustedOrigin(o); err != nil {
			panic(err)
		}
	}
	protect := sdkauth.RequireBearerToken(Verifier(resource, key), &sdkauth.RequireBearerTokenOptions{
		ResourceMetadataURL: MetadataURL(resource),
		Scopes:              []string{scope}, // checked only on a token the verifier accepted: 403
	})
	mux := http.NewServeMux()
	mux.Handle(PRMPath, sdkauth.ProtectedResourceMetadataHandler(&oauthex.ProtectedResourceMetadata{
		Resource: resource, AuthorizationServers: []string{Issuer},
		ScopesSupported: []string{scope}, BearerMethodsSupported: []string{"header"}}))
	mux.Handle("/mcp", CheckLocal(LocalHosts(port), LocalOrigins(port), cop.Handler(CapBody(withErrorCodes(protect, mcpHandler)))))
	return mux
}
