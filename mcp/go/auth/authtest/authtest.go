// Package authtest mints access tokens for tests. TEST ONLY: import it from _test.go files, never
// from a server. It is the Go twin of auth_server.mint_test_token (mcp/python): same issuer, same
// test key, same claims, and nothing here is served over HTTP.
package authtest

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"time"

	"lumen-mcp/auth"
)

// Token is a valid token for `audience` (a resource URI) with `scope`, as the test AS would issue it.
func Token(scope, audience, clientID, sub string) string {
	now := time.Now().Unix()
	return Sign(map[string]any{"iss": auth.Issuer, "aud": audience, "sub": sub, "client_id": clientID,
		"scope": scope, "iat": now, "exp": now + 600})
}

// Sign signs any claims with the test key (HS256), for tests that need a broken token.
func Sign(claims map[string]any) string { return SignWith(claims, auth.TestSigningKey, "HS256") }

// SignWith signs with any key and header alg. alg "none" gives an unsigned token (empty signature).
func SignWith(claims map[string]any, key, alg string) string {
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
	m := hmac.New(sha256.New, []byte(key))
	m.Write([]byte(head))
	return head + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}
