package core

import (
	"encoding/json"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// ── 1. Tickets ──────────────────────────────────────────────────────────────
func TestLoadTicketsCount(t *testing.T) {
	ts, err := LoadTickets()
	if err != nil {
		t.Fatal(err)
	}
	if len(ts) != 61 || ts[60].ID != "t061" {
		t.Fatalf("got %d tickets, last %q", len(ts), ts[len(ts)-1].ID)
	}
}

func TestSearchRanking(t *testing.T) {
	hits, err := Search("refund duplicate", DefaultLimit)
	if err != nil || len(hits) == 0 || hits[0].ID != "t001" {
		t.Fatalf("hits=%v err=%v", hits, err)
	}
	raw, _ := json.Marshal(hits[0])
	var m map[string]any
	json.Unmarshal(raw, &m)
	keys := []string{}
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if !reflect.DeepEqual(keys, []string{"category", "id", "priority", "untrusted_body"}) {
		t.Fatalf("hit keys: %v", keys)
	}
	three, _ := Search("the", 3)
	if len(three) != 3 {
		t.Fatalf("limit: got %d", len(three))
	}
}

func TestUntrustedBodyVerbatim(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(Root(), "mcp", "data", "injection.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var row struct{ Text string }
	json.Unmarshal([]byte(strings.Split(string(raw), "\n")[0]), &row)
	got, err := Get("t061")
	if err != nil || got.UntrustedBody != row.Text {
		t.Fatalf("body not verbatim: %v", err)
	}
	if !strings.Contains(row.Text, "ignore prior instructions") {
		t.Fatal("canary text missing")
	}
}

func TestGetUnknownErrors(t *testing.T) {
	if _, err := Get("t999"); err == nil {
		t.Fatal("want error")
	}
}

// ── 2. Classifier ───────────────────────────────────────────────────────────
func TestTokenizeMatchesFixture(t *testing.T) {
	var cases []struct {
		Name, Input string
		Tokens      []string
	}
	readJSON(t, filepath.Join(Root(), "ml", "data", "fixtures", "tokenize.json"), &cases)
	if len(cases) == 0 {
		t.Fatal("no cases")
	}
	m, err := loadModel()
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		got := tokenize(c.Input, &m.Tokenizer)
		if len(got) == 0 && len(c.Tokens) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, c.Tokens) {
			t.Errorf("%s: got %q want %q", c.Name, got, c.Tokens)
		}
	}
}

func TestClassifyMatchesFixture(t *testing.T) {
	var f struct {
		Cases []struct {
			Name     string
			Input    string
			Expected struct {
				Label         string             `json:"label"`
				Scores        map[string]float64 `json:"scores"`
				LowConfidence bool               `json:"low_confidence"`
			}
		}
	}
	readJSON(t, filepath.Join(Root(), "ml", "data", "fixtures", "predictions.json"), &f)
	if len(f.Cases) != 30 {
		t.Fatalf("want 30 cases, got %d", len(f.Cases))
	}
	for _, c := range f.Cases {
		got, err := Classify(c.Input)
		if err != nil {
			t.Fatal(err)
		}
		if got.Label != c.Expected.Label || got.LowConfidence != c.Expected.LowConfidence || got.ModelVersion == "" {
			t.Errorf("%s: got (%s, %v)", c.Name, got.Label, got.LowConfidence)
		}
		if len(got.Scores) != len(c.Expected.Scores) {
			t.Errorf("%s: score count", c.Name)
		}
		for k, want := range c.Expected.Scores {
			if math.Abs(got.Scores[k]-want) > 1e-9 {
				t.Errorf("%s: %s = %v want %v", c.Name, k, got.Scores[k], want)
			}
		}
	}
}

func readJSON(t *testing.T, path string, v any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, v); err != nil {
		t.Fatal(err)
	}
}

// ── 3. Labels ───────────────────────────────────────────────────────────────
func agent() LabelInput { return LabelInput{Source: "agent", ClientID: "c1"} }

func TestWriteLabelRejectsBadLabel(t *testing.T) {
	p := filepath.Join(t.TempDir(), "l.jsonl")
	in := agent()
	in.Path = p
	if _, err := WriteLabel("t001", "nonsense", in); err == nil {
		t.Fatal("want error")
	}
	if _, err := os.Stat(p); err == nil {
		t.Fatal("file must not exist")
	}
	in.Source = "robot"
	if _, err := WriteLabel("t001", "bug", in); err == nil {
		t.Fatal("want source error")
	}
}

func TestWriteLabelRowShape(t *testing.T) {
	p := filepath.Join(t.TempDir(), "l.jsonl")
	in := LabelInput{Path: p, Source: "human", ClientID: "c1", Confirmed: true}
	row, err := WriteLabel("t001", "billing", in)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(p)
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	keys := []string{}
	for k := range got {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	want := []string{"client_id", "confirmed", "label", "model", "source", "ticket_id", "time"}
	if !reflect.DeepEqual(keys, want) {
		t.Fatalf("keys %v", keys)
	}
	if got["model"] != nil || got["time"] != row.Time || !strings.HasSuffix(row.Time, "+00:00") {
		t.Fatalf("row %v / %v", got, row)
	}
}

func TestEligibleForEval(t *testing.T) {
	cases := []struct {
		src  string
		conf bool
		want bool
	}{{"human", true, true}, {"human", false, false}, {"agent", true, false}, {"agent", false, false}}
	for _, c := range cases {
		if EligibleForEval(LabelRow{Source: c.src, Confirmed: c.conf}) != c.want {
			t.Errorf("%v", c)
		}
	}
}

// The concurrency test uses real processes: the test binary re-executes itself twice
// (LUMEN_APPEND_PATH set), so the two writers have separate file descriptions and the
// flock is what keeps lines whole, as with two MCP servers. Goroutines in one process
// would share nothing that matters here.
func TestHelperAppend(t *testing.T) {
	p, tag := os.Getenv("LUMEN_APPEND_PATH"), os.Getenv("LUMEN_APPEND_TAG")
	if p == "" {
		t.Skip("helper process only")
	}
	model := strings.Repeat("m", 2000)
	for i := 0; i < 50; i++ {
		_, err := WriteLabel("t001", "bug", LabelInput{Path: p, Source: "agent", ClientID: tag, Model: &model})
		if err != nil {
			t.Fatal(err)
		}
	}
}

func TestConcurrentLabelAppends(t *testing.T) {
	p := filepath.Join(t.TempDir(), "l.jsonl")
	var cmds []*exec.Cmd
	for _, tag := range []string{"a", "b"} {
		c := exec.Command(os.Args[0], "-test.run=^TestHelperAppend$")
		c.Env = append(os.Environ(), "LUMEN_APPEND_PATH="+p, "LUMEN_APPEND_TAG="+tag)
		if err := c.Start(); err != nil {
			t.Fatal(err)
		}
		cmds = append(cmds, c)
	}
	for _, c := range cmds {
		if err := c.Wait(); err != nil {
			t.Fatal(err)
		}
	}
	raw, _ := os.ReadFile(p)
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	if len(lines) != 100 {
		t.Fatalf("want 100 lines, got %d", len(lines))
	}
	count := map[string]int{}
	for _, l := range lines {
		var r LabelRow
		if err := json.Unmarshal([]byte(l), &r); err != nil {
			t.Fatalf("interleaved line: %v", err)
		}
		count[r.ClientID]++
	}
	if count["a"] != 50 || count["b"] != 50 {
		t.Fatalf("counts %v", count)
	}
}
