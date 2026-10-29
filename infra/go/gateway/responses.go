// responses: the Go twin of infra/python/responses.py, what the gateway reads from and writes
// into bodies: the canonical JSON, a request's text and cache key, an error body, and the usage,
// stop_reason and retry-after of an answer. The twin reads JSON answers only (no SSE streams):
// the fixture cases never stream.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"strconv"
	"strings"
)

// Canonical is responses.canonical: sorted keys, no spaces, UTF-8 unescaped.
func Canonical(v any) []byte { return []byte(Dumps(v, true)) }

// messagesText is the messages' text in order: string contents and text blocks.
func messagesText(body *Object) string {
	var parts []string
	ms, _ := body.MustGet("messages").([]any)
	for _, m := range ms {
		mo, ok := m.(*Object)
		if !ok {
			continue
		}
		switch c := mo.MustGet("content").(type) {
		case string:
			parts = append(parts, c)
		case []any:
			for _, b := range c {
				if bo, ok := b.(*Object); ok {
					if s, ok := bo.MustGet("text").(string); ok {
						parts = append(parts, s)
					}
				}
			}
		}
	}
	return strings.Join(parts, "\n")
}

// CacheKey is the sha256 hex of the path, a newline and the body bytes as received.
func CacheKey(path string, body []byte) string {
	h := sha256.New()
	h.Write([]byte(path + "\n"))
	h.Write(body)
	return hex.EncodeToString(h.Sum(nil))
}

// retryAfterMs is an answer's retry-after (seconds) in ms; 0 when absent or unreadable.
func retryAfterMs(headers map[string]string) int {
	for k, v := range headers {
		if strings.ToLower(k) == "retry-after" {
			f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
			if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
				return 0
			}
			return max(0, int(f*1000))
		}
	}
	return 0
}

var usageFields = []string{"input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"}

func jsonBody(body []byte) *Object {
	v, err := ParseJSON(body)
	if err != nil {
		return nil
	}
	o, _ := v.(*Object)
	return o
}

func errorBody(kind, message string) []byte {
	b, _ := json.Marshal(map[string]any{"type": "error", "error": map[string]string{"type": kind, "message": message}})
	return b
}

// responseUsage is the four usage fields of a JSON answer (a missing one is 0), or nil. The
// twin reads JSON answers only: the fixture cases never stream.
func responseUsage(body []byte) map[string]int {
	d := jsonBody(body)
	if d == nil {
		return nil
	}
	u, ok := d.MustGet("usage").(*Object)
	if !ok {
		return nil
	}
	out := map[string]int{}
	for _, k := range usageFields {
		if n, ok := u.MustGet(k).(Number); ok {
			out[k] = int(n.Float())
		} else {
			out[k] = 0
		}
	}
	return out
}

func stopReason(body []byte) string {
	if d := jsonBody(body); d != nil {
		s, _ := d.MustGet("stop_reason").(string)
		return s
	}
	return ""
}

func copyHeaders(h map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range h {
		out[k] = v
	}
	return out
}

func (o *Object) getString(k string) (string, bool) {
	if o == nil {
		return "", false
	}
	s, ok := o.MustGet(k).(string)
	return s, ok
}
