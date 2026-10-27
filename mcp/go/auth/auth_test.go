package auth_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	sdkauth "github.com/modelcontextprotocol/go-sdk/auth"

	"lumen-mcp/auth"
	"lumen-mcp/auth/authtest"
)

func good(resource, scope string) map[string]any {
	now := time.Now().Unix()
	return map[string]any{"iss": auth.Issuer, "aud": resource, "sub": "user-1", "client_id": "lumen-cli",
		"scope": scope, "iat": now, "exp": now + 600}
}

func with(c map[string]any, k string, v any) map[string]any {
	out := map[string]any{}
	for kk, vv := range c {
		out[kk] = vv
	}
	if v == nil {
		delete(out, k)
	} else {
		out[k] = v
	}
	return out
}

// ── 1. Token verifier ───────────────────────────────────────────────────────
func TestVerifierAcceptsAGoodToken(t *testing.T) {
	v := auth.Verifier(auth.OpsResource)
	ti, err := v(context.Background(), authtest.Token(auth.ScopeWrite, auth.OpsResource, "c-1", "u-9"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if ti.UserID != "u-9" || ti.Extra["client_id"] != "c-1" || len(ti.Scopes) != 1 || ti.Scopes[0] != auth.ScopeWrite ||
		ti.Expiration.IsZero() {
		t.Fatalf("token info %+v", ti)
	}
}

func TestVerifierRejects(t *testing.T) {
	now := time.Now().Unix()
	base := good(auth.OpsResource, auth.ScopeWrite)
	cases := map[string]string{
		"garbage":        "not-a-jwt",
		"wrong_key":      authtest.SignWith(base, "another-key-that-is-long-enough-for-hs256!!", "HS256"),
		"alg_none":       authtest.SignWith(base, "", "none"),
		"expired":        authtest.Sign(with(with(base, "exp", now-3600), "iat", now-4000)),
		"not_yet_valid":  authtest.Sign(with(base, "nbf", now+3600)),
		"issued_future":  authtest.Sign(with(base, "iat", now+3600)),
		"wrong_issuer":   authtest.Sign(with(base, "iss", "http://127.0.0.1:9001")),
		"no_exp":         authtest.Sign(with(base, "exp", nil)),
		"no_aud":         authtest.Sign(with(base, "aud", nil)),
		"no_sub":         authtest.Sign(with(base, "sub", nil)),
		"no_client_id":   authtest.Sign(with(base, "client_id", nil)),
		"other_audience": authtest.Sign(with(base, "aud", auth.ReadResource)),
		"aud_list_other": authtest.Sign(with(base, "aud", []string{auth.ReadResource, "http://x.invalid/mcp"})),
		"aud_number":     authtest.Sign(with(base, "aud", 7)),
	}
	v := auth.Verifier(auth.OpsResource)
	for name, tok := range cases {
		if _, err := v(context.Background(), tok, nil); !errors.Is(err, sdkauth.ErrInvalidToken) {
			t.Errorf("%s: want ErrInvalidToken (401), got %v", name, err)
		}
	}
	// RFC 7519: aud may be a list; one entry naming this server is enough. A trailing slash is ignored.
	for _, aud := range []any{[]string{auth.ReadResource, auth.OpsResource}, auth.OpsResource + "/"} {
		if _, err := v(context.Background(), authtest.Sign(with(base, "aud", aud)), nil); err != nil {
			t.Errorf("aud %v: %v", aud, err)
		}
	}
}

// ── 2. Resource-server handler ──────────────────────────────────────────────
var okHandler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
	ti := sdkauth.TokenInfoFromContext(r.Context())
	w.Write([]byte(ti.Extra["client_id"].(string)))
})

func do(h http.Handler, method, path, token, origin string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, "http://127.0.0.1:8766"+path, strings.NewReader(`{}`))
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestHandlerStatuses(t *testing.T) {
	h := auth.Handler(okHandler, auth.OpsResource, auth.ScopeWrite)
	tok := authtest.Token(auth.ScopeWrite, auth.OpsResource, "lumen-cli", "user-1")
	if rec := do(h, "POST", "/mcp", tok, ""); rec.Code != 200 || rec.Body.String() != "lumen-cli" {
		t.Errorf("good token: %d %q", rec.Code, rec.Body.String())
	}
	rec := do(h, "POST", "/mcp", "", "")
	want := `resource_metadata="http://127.0.0.1:8766/.well-known/oauth-protected-resource/mcp"`
	if rec.Code != 401 || !strings.Contains(rec.Header().Get("WWW-Authenticate"), want) {
		t.Errorf("no token: %d %q", rec.Code, rec.Header().Get("WWW-Authenticate"))
	}
	if rec := do(h, "POST", "/mcp", authtest.Token(auth.ScopeRead, auth.OpsResource, "c", "u"), ""); rec.Code != 403 {
		t.Errorf("wrong scope: %d", rec.Code)
	}
	if rec := do(h, "POST", "/mcp", authtest.Token(auth.ScopeWrite, auth.ReadResource, "c", "u"), ""); rec.Code != 401 {
		t.Errorf("wrong audience: %d", rec.Code)
	}
	if rec := do(h, "POST", "/mcp?access_token="+tok, "", ""); rec.Code != 401 {
		t.Errorf("token in the query string must be ignored: %d", rec.Code)
	}
}

func TestOriginCheckedForEveryMethod(t *testing.T) {
	// R18: http.CrossOriginProtection lets safe methods (GET, HEAD, OPTIONS) through; ours does not.
	h := auth.Handler(okHandler, auth.OpsResource, auth.ScopeWrite)
	tok := authtest.Token(auth.ScopeWrite, auth.OpsResource, "lumen-cli", "user-1")
	for _, m := range []string{"GET", "HEAD", "OPTIONS", "POST", "DELETE"} {
		for _, token := range []string{tok, ""} { // Go checks Origin before auth: 403 with or without a token
			if rec := do(h, m, "/mcp", token, "http://evil.example"); rec.Code != 403 {
				t.Errorf("%s bad Origin (token %v): %d", m, token != "", rec.Code)
			}
		}
		if rec := do(h, m, "/mcp", tok, "http://127.0.0.1:6274"); rec.Code == 403 {
			t.Errorf("%s Inspector origin: 403", m)
		}
		if rec := do(h, m, "/mcp", tok, "http://127.0.0.1:8766"); rec.Code == 403 {
			t.Errorf("%s own origin: 403", m)
		}
	}
	for _, o := range []string{"http://127.0.0.1:9999", "null", "http://127.0.0.1:8766.evil.example", "https://127.0.0.1:8766"} {
		if rec := do(h, "GET", "/mcp", tok, o); rec.Code != 403 {
			t.Errorf("GET Origin %q: %d", o, rec.Code)
		}
	}
}

func TestProtectedResourceMetadata(t *testing.T) {
	h := auth.Handler(okHandler, auth.ReadResource, auth.ScopeRead)
	rec := do(h, "GET", "/.well-known/oauth-protected-resource/mcp", "", "")
	var prm map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &prm); err != nil || rec.Code != 200 {
		t.Fatalf("%d %v", rec.Code, err)
	}
	if prm["resource"] != auth.ReadResource || prm["authorization_servers"].([]any)[0] != auth.Issuer ||
		prm["scopes_supported"].([]any)[0] != auth.ScopeRead || prm["bearer_methods_supported"].([]any)[0] != "header" {
		t.Errorf("PRM %v", prm)
	}
}

func TestBodyCapBeforeAuth(t *testing.T) {
	h := auth.Handler(okHandler, auth.OpsResource, auth.ScopeWrite)
	req := httptest.NewRequest("POST", "http://127.0.0.1:8766/mcp", strings.NewReader(strings.Repeat(" ", 131073)))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("over the cap, no token: %d", rec.Code)
	}
}
