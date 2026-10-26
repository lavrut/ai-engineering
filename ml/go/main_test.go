package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"unicode/utf8"
)

// The tests read the committed model, fixtures and reports: Python wrote them, Go must agree.
const root = ".."

const (
	lengthBody = `{"error":"text must be 1 to 20000 characters"}`
	malformed  = `{"error":"body must be a JSON object with a string field \"text\""}`
)

var (
	modelOnce sync.Once
	prod      *Model
	prodErr   error
)

func model(t *testing.T) *Model {
	t.Helper()
	modelOnce.Do(func() { prod, prodErr = loadProduction(root) })
	if prodErr != nil {
		t.Fatal(prodErr)
	}
	return prod
}

func readJSON(t *testing.T, path string, v any) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, v); err != nil {
		t.Fatal(err)
	}
}

// ── Tokenizer, scores and PSI against the Python fixtures ──────────────────
func TestTokenizeMatchesFixture(t *testing.T) {
	var cases []struct {
		Name, Input string
		Tokens      []string
	}
	readJSON(t, filepath.Join(root, "data", "fixtures", "tokenize.json"), &cases)
	if len(cases) == 0 {
		t.Fatal("no cases")
	}
	cfg := &model(t).Tokenizer
	for _, c := range cases {
		got := tokenize(c.Input, cfg)
		if len(got) == 0 && len(c.Tokens) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, c.Tokens) {
			t.Errorf("%s: tokenize(%q) = %q, want %q", c.Name, c.Input, got, c.Tokens)
		}
	}
}

type fixtureCase struct {
	Name     string
	Input    string
	Expected struct {
		Label         string             `json:"label"`
		Scores        map[string]float64 `json:"scores"`
		LowConfidence bool               `json:"low_confidence"`
	}
}

func predictionCases(t *testing.T) []fixtureCase {
	var f struct{ Cases []fixtureCase }
	readJSON(t, filepath.Join(root, "data", "fixtures", "predictions.json"), &f)
	if len(f.Cases) == 0 {
		t.Fatal("no cases")
	}
	return f.Cases
}

func checkPrediction(t *testing.T, c fixtureCase, got Prediction) float64 {
	t.Helper()
	if got.Label != c.Expected.Label || got.LowConfidence != c.Expected.LowConfidence {
		t.Errorf("%s: got (%s, %v), want (%s, %v)", c.Name, got.Label, got.LowConfidence,
			c.Expected.Label, c.Expected.LowConfidence)
	}
	if len(got.Scores) != len(c.Expected.Scores) {
		t.Errorf("%s: %d scores, want %d", c.Name, len(got.Scores), len(c.Expected.Scores))
	}
	worst := 0.0
	for k, want := range c.Expected.Scores {
		d := math.Abs(got.Scores[k] - want)
		if _, ok := got.Scores[k]; !ok || d > 1e-9 {
			t.Errorf("%s: score %s = %v, want %v", c.Name, k, got.Scores[k], want)
		}
		worst = math.Max(worst, d)
	}
	return worst
}

func TestScoreMatchesFixture(t *testing.T) {
	m := model(t)
	worst := 0.0
	for _, c := range predictionCases(t) {
		worst = math.Max(worst, checkPrediction(t, c, m.score(c.Input)))
	}
	t.Logf("max |score diff| vs predictions.json: %.3g", worst)
}

func TestPSIMatchesFixture(t *testing.T) {
	var f struct {
		Epsilon float64
		Cases   []struct {
			Name             string
			Expected, Actual []float64
			PSI              float64
		}
	}
	readJSON(t, filepath.Join(root, "data", "fixtures", "psi_cases.json"), &f)
	if len(f.Cases) == 0 || f.Epsilon != 1e-4 {
		t.Fatalf("bad fixture: %d cases, epsilon %v", len(f.Cases), f.Epsilon)
	}
	worst := 0.0
	for _, c := range f.Cases {
		got := psi(c.Expected, c.Actual, f.Epsilon)
		if worst = math.Max(worst, math.Abs(got-c.PSI)); math.Abs(got-c.PSI) > 1e-12 {
			t.Errorf("%s: psi = %.17g, want %.17g", c.Name, got, c.PSI)
		}
	}
	t.Logf("max |PSI diff| vs psi_cases.json: %.3g", worst)
}

func TestPySumIsCompensated(t *testing.T) {
	// Python 3.12+ sum() is Neumaier-compensated: ten 0.1s add up to exactly 1.0.
	xs := make([]float64, 10)
	for i := range xs {
		xs[i] = 0.1
	}
	if got := pySum(xs); got != 1.0 {
		t.Fatalf("pySum = %.17g, want 1", got)
	}
}

// ── Drift against drift.json ────────────────────────────────────────────────
type reportPeriod struct {
	Month, Quarter  string
	N               int
	PSIPredictions  float64   `json:"psi_predictions"`
	PSIWords        float64   `json:"psi_words"`
	AlertConvention bool      `json:"alert_convention"`
	AlertCalibrated bool      `json:"alert_calibrated"`
	TopShiftedWords []Shifted `json:"top_shifted_words"`
}

func TestDriftMatchesReport(t *testing.T) {
	var rep struct{ Months, Quarters []reportPeriod }
	readJSON(t, filepath.Join(root, "reports", "drift.json"), &rep)
	got, err := computeDrift(root, model(t))
	if err != nil {
		t.Fatal(err)
	}
	worst := 0.0
	compare := func(unit string, want []reportPeriod, have []Period) {
		if len(want) == 0 || len(want) != len(have) {
			t.Fatalf("%s: %d periods, drift.json has %d", unit, len(have), len(want))
		}
		for i, w := range want {
			h := have[i]
			key := w.Month + w.Quarter
			if h.Key != key || h.N != w.N {
				t.Errorf("%s %d: got %s n=%d, want %s n=%d", unit, i, h.Key, h.N, key, w.N)
			}
			for _, d := range []float64{h.PSIPredictions - w.PSIPredictions, h.PSIWords - w.PSIWords} {
				worst = math.Max(worst, math.Abs(d))
				if math.Abs(d) > 1e-9 {
					t.Errorf("%s %s: psi differs by %g", unit, key, d)
				}
			}
			if h.AlertConvention != w.AlertConvention || h.AlertCalibrated != w.AlertCalibrated {
				t.Errorf("%s %s: flags (%v, %v), want (%v, %v)", unit, key, h.AlertConvention,
					h.AlertCalibrated, w.AlertConvention, w.AlertCalibrated)
			}
			if len(h.TopShiftedWords) != len(w.TopShiftedWords) {
				t.Fatalf("%s %s: %d shifted words, want %d", unit, key, len(h.TopShiftedWords), len(w.TopShiftedWords))
			}
			for j, sw := range w.TopShiftedWords {
				hw := h.TopShiftedWords[j]
				if hw.Word != sw.Word || hw.Direction != sw.Direction || math.Abs(hw.Contribution-sw.Contribution) > 1e-9 {
					t.Errorf("%s %s word %d: got %+v, want %+v", unit, key, j, hw, sw)
				}
			}
		}
	}
	compare("month", rep.Months, got.Months)
	compare("quarter", rep.Quarters, got.Quarters)
	t.Logf("max |PSI diff| vs drift.json: %.3g", worst)
}

// ── The HTTP contract (task-6 report, fix round 1) ──────────────────────────
type reply struct {
	status int
	body   string
}

func post(t *testing.T, url string, body []byte, contentType string) reply {
	t.Helper()
	req, err := http.NewRequest("POST", url+"/predict", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return reply{resp.StatusCode, string(b)}
}

func textBody(text string) []byte {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.Encode(map[string]string{"text": text})
	return bytes.TrimSuffix(buf.Bytes(), []byte("\n"))
}

func utf16le(s string) []byte {
	out := []byte{0xff, 0xfe}
	for _, r := range s {
		out = append(out, byte(r), 0)
	}
	return out
}

func utf32le(s string) []byte {
	out := []byte{0xff, 0xfe, 0, 0}
	for _, r := range s {
		out = append(out, byte(r), 0, 0, 0)
	}
	return out
}

func TestContract(t *testing.T) {
	srv := httptest.NewServer(newHandler(model(t)))
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 || string(b) != `{"status":"ok","model_version":"v1"}` {
		t.Errorf("GET /health = %d %s", resp.StatusCode, b)
	}

	base := []byte(`{"text":"hi"}`)
	pad := func(n int) []byte {
		return append(append([]byte{}, base...), bytes.Repeat([]byte(" "), n-len(base))...)
	}
	emoji := "\U0001F600"
	cases := []struct {
		name        string
		body        []byte
		contentType string
		status      int
		want        string // exact body for 400s; "" means any 200 prediction
	}{
		{"valid text", []byte(`{"text":"my mortgage payment was misapplied"}`), "application/json", 200, ""},
		{"whitespace only is valid", textBody("   \n\t "), "application/json", 200, ""},
		{"empty text", []byte(`{"text":""}`), "application/json", 400, lengthBody},
		{"20000 a", textBody(strings.Repeat("a", 20000)), "", 200, ""},
		{"20001 a", textBody(strings.Repeat("a", 20001)), "", 400, lengthBody},
		{"20000 two-byte chars", textBody(strings.Repeat("é", 20000)), "", 200, ""},
		{"20000 four-byte emoji", textBody(strings.Repeat(emoji, 20000)), "", 200, ""},
		{"20001 four-byte emoji", textBody(strings.Repeat(emoji, 20001)), "", 400, lengthBody},
		{"body of exactly 131072 bytes", pad(131072), "", 200, ""},
		{"body of 131073 bytes", pad(131073), "", 400, lengthBody},
		{"empty body", []byte(""), "application/json", 400, malformed},
		{"not json", []byte("not json"), "application/json", 400, malformed},
		{"array body", []byte(`[]`), "application/json", 400, malformed},
		{"array of objects", []byte(`[{"text":"hi"}]`), "application/json", 400, malformed},
		{"string body", []byte(`"text"`), "application/json", 400, malformed},
		{"null body", []byte(`null`), "application/json", 400, malformed},
		{"empty object", []byte(`{}`), "application/json", 400, malformed},
		{"text is a number", []byte(`{"text": 5}`), "application/json", 400, malformed},
		{"text is null", []byte(`{"text": null}`), "application/json", 400, malformed},
		{"text is an array", []byte(`{"text": ["a"]}`), "application/json", 400, malformed},
		{"text is an object", []byte(`{"text": {"a": "b"}}`), "application/json", 400, malformed},
		{"text missing", []byte(`{"txt": "hi"}`), "application/json", 400, malformed},
		{"key case matters", []byte(`{"TEXT": "hi"}`), "application/json", 400, malformed},
		{"invalid UTF-8 bytes", []byte("\xff\xfe"), "application/json", 400, malformed},
		{"invalid UTF-8 inside the string", []byte("{\"text\":\"a\xffb\"}"), "", 400, malformed},
		{"UTF-8 encoded surrogate", []byte("{\"text\":\"a\xed\xa0\x80b\"}"), "", 400, malformed},
		{"UTF-8 BOM", append([]byte("\xef\xbb\xbf"), base...), "", 400, malformed},
		{"UTF-16", utf16le(`{"text": "hi"}`), "", 400, malformed},
		{"UTF-32", utf32le(`{"text": "hi"}`), "", 400, malformed},
		{"trailing data", []byte(`{"text":"hi"} x`), "", 400, malformed},
		{"no Content-Type", base, "", 200, ""},
		{"text/plain", base, "text/plain", 200, ""},
		{"extra fields ignored", []byte(`{"text":"hi","x":1,"y":[1,{"z":null}]}`), "", 200, ""},
		{"huge number in an extra field", []byte(`{"text":"hi","x":1e400}`), "", 200, ""},
		{"escaped key", []byte(`{"text":"mortgage"}`), "", 200, ""},
		{"duplicate keys: last (empty) wins", []byte(`{"text":"first","text":""}`), "", 400, lengthBody},
		{"duplicate keys: last (valid) wins", []byte(`{"text":"","text":"mortgage"}`), "", 200, ""},
		{"duplicate keys: last (number) wins", []byte(`{"text":"hi","text":5}`), "", 400, malformed},
	}
	for _, c := range cases {
		got := post(t, srv.URL, c.body, c.contentType)
		if got.status != c.status {
			t.Errorf("%s: status %d, want %d (%s)", c.name, got.status, c.status, got.body)
			continue
		}
		if c.want != "" && got.body != c.want {
			t.Errorf("%s: body %s, want %s", c.name, got.body, c.want)
		}
		if c.status == 200 {
			var p Prediction
			if err := json.Unmarshal([]byte(got.body), &p); err != nil || p.ModelVersion != "v1" || len(p.Scores) != 6 {
				t.Errorf("%s: bad prediction %s", c.name, got.body)
			}
		}
	}
	if len(pad(131072)) != 131072 || len(pad(131073)) != 131073 || len(textBody(strings.Repeat(emoji, 20001))) > 131072 {
		t.Fatal("size cases are not what they claim")
	}

	// The last duplicate wins: the response is the prediction for "mortgage".
	dup := post(t, srv.URL, []byte(`{"text":"","text":"mortgage"}`), "")
	var p Prediction
	json.Unmarshal([]byte(dup.body), &p)
	if want := model(t).score("mortgage"); p.Label != want.Label || p.Scores[want.Label] != want.Scores[want.Label] {
		t.Errorf("duplicate keys: got %s", dup.body)
	}
}

func TestPredictionsOverHTTP(t *testing.T) {
	srv := httptest.NewServer(newHandler(model(t)))
	defer srv.Close()
	worst := 0.0
	for _, c := range predictionCases(t) {
		got := post(t, srv.URL, textBody(c.Input), "application/json")
		if n := utf8.RuneCountInString(c.Input); n < 1 || n > 20000 {
			if got.status != 400 || got.body != lengthBody {
				t.Errorf("%s: %d %s, want 400 LENGTH", c.Name, got.status, got.body)
			}
			continue
		}
		var p Prediction
		if got.status != 200 || json.Unmarshal([]byte(got.body), &p) != nil {
			t.Fatalf("%s: %d %s", c.Name, got.status, got.body)
		}
		worst = math.Max(worst, checkPrediction(t, c, p))
	}
	t.Logf("max |score diff| over HTTP: %.3g", worst)
}

// ── Startup failures (the same as server.py) ────────────────────────────────
func tempRoot(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "models"), 0o755)
	for _, name := range []string{"registry.json", filepath.Join("models", "v1.json")} {
		b, err := os.ReadFile(filepath.Join(root, name))
		if err != nil {
			t.Fatal(err)
		}
		os.WriteFile(filepath.Join(dir, name), b, 0o644)
	}
	return dir
}

func editJSON(t *testing.T, path string, edit func(map[string]any)) {
	t.Helper()
	var v map[string]any
	readJSON(t, path, &v)
	edit(v)
	b, _ := json.Marshal(v)
	os.WriteFile(path, b, 0o644)
}

func TestStartupFailures(t *testing.T) {
	versions := func(v map[string]any) map[string]any { return v["versions"].(map[string]any) }
	cases := []struct {
		name, want string
		breakIt    func(dir string)
	}{
		{"missing registry", "registry not found", func(dir string) {
			os.Remove(filepath.Join(dir, "registry.json"))
		}},
		{"unreadable registry", "registry unreadable", func(dir string) {
			os.WriteFile(filepath.Join(dir, "registry.json"), []byte("{not json"), 0o644)
		}},
		{"no production version", "production", func(dir string) {
			editJSON(t, filepath.Join(dir, "registry.json"), func(v map[string]any) {
				versions(v)["v1"].(map[string]any)["stage"] = "archived"
			})
		}},
		{"missing model file", "model file", func(dir string) {
			os.Remove(filepath.Join(dir, "models", "v1.json"))
		}},
		{"bad hash", "SHA-256 mismatch", func(dir string) {
			f, _ := os.OpenFile(filepath.Join(dir, "models", "v1.json"), os.O_APPEND|os.O_WRONLY, 0)
			f.WriteString(" ")
			f.Close()
		}},
		{"version mismatch", "v9", func(dir string) {
			p := filepath.Join(dir, "models", "v1.json")
			editJSON(t, p, func(v map[string]any) { v["version"] = "v9" })
			b, _ := os.ReadFile(p)
			sum := sha256.Sum256(b)
			editJSON(t, filepath.Join(dir, "registry.json"), func(v map[string]any) {
				versions(v)["v1"].(map[string]any)["sha256"] = hex.EncodeToString(sum[:])
			})
		}},
	}
	if _, err := loadProduction(tempRoot(t)); err != nil {
		t.Fatalf("untouched copy should load: %v", err)
	}
	for _, c := range cases {
		dir := tempRoot(t)
		c.breakIt(dir)
		_, err := loadProduction(dir)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: err = %v, want it to mention %q", c.name, err, c.want)
		}
	}
}
