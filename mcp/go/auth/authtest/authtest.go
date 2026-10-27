// Package authtest gives tests a signing key and tokens. TEST ONLY: import it from _test.go files,
// never from a server. It is the Go twin of the Python tests' per-test key (conftest.py) and of
// auth_server.mint_test_token: same issuer, same claims, a fresh random key per test, and nothing
// here is served over HTTP.
package authtest

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"lumen-mcp/auth"
)

// UseKey gives this test its own random key: written to a temp file in the AS's format (hex, mode
// 0600) and named by LUMEN_SIGNING_KEY_FILE for the rest of the test. Returns the key.
func UseKey(t testing.TB) []byte {
	t.Helper()
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "signing.key")
	if err := os.WriteFile(path, []byte(hex.EncodeToString(key)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(auth.KeyFileEnv, path)
	return key
}

// Token is a valid token for `audience` (a resource URI) with `scope`, signed with `key`, as the
// test AS would issue it.
func Token(key []byte, scope, audience, clientID, sub string) string {
	now := time.Now().Unix()
	return Sign(key, map[string]any{"iss": auth.Issuer, "aud": audience, "sub": sub, "client_id": clientID,
		"scope": scope, "iat": now, "exp": now + 600})
}

// Sign signs any claims with `key` (HS256), for tests that need a broken token.
func Sign(key []byte, claims map[string]any) string { return SignWith(claims, key, "HS256") }

// SignWith signs with any key and header alg. alg "none" gives an unsigned token (empty signature).
func SignWith(claims map[string]any, key []byte, alg string) string {
	enc := func(v any) string {
		b, err := json.Marshal(v)
		if err != nil {
			panic(err)
		}
		return base64.RawURLEncoding.EncodeToString(b)
	}
	head := enc(map[string]string{"alg": alg, "typ": "JWT"}) + "." + enc(claims)
	if alg == "none" {
		return head + "."
	}
	m := hmac.New(sha256.New, key)
	m.Write([]byte(head))
	return head + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}
