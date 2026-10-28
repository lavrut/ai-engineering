package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/anthropics/anthropic-sdk-go"
)

// The fixture root holds tiny hand-made data; expected_scores.json was worked out by hand
// and is shared with the Python tests, so both languages must produce the same numbers.
var fixtures = filepath.Join(dataDir, "fixtures")

// must fails the test (by panicking) on an error, so calls can be written inline.
func must[T any](v T, err error) T {
	if err != nil {
		panic(err)
	}
	return v
}

func expected(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	b := must(os.ReadFile(filepath.Join(fixtures, "expected_scores.json")))
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func field[T any](t *testing.T, raw json.RawMessage) T {
	t.Helper()
	var v T
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func ticketsByID(t *testing.T) map[string]Ticket {
	t.Helper()
	by := map[string]Ticket{}
	for _, tk := range must(loadTickets(filepath.Join(fixtures, "tickets.jsonl"))) {
		by[tk.ID] = tk
	}
	return by
}

// graded grades every saved line of one fixture file; rep < 0 means all reps.
func graded(t *testing.T, name string, rep int) []Grade {
	t.Helper()
	by := ticketsByID(t)
	lines := must(loadJSONL[Line](filepath.Join(fixtures, "responses", name+".jsonl")))
	var out []Grade
	for _, l := range lines {
		if rep < 0 || l.Config.Rep == rep {
			out = append(out, grade(by[l.CaseID], l))
		}
	}
	return out
}

const (
	v1File = "triage__claude-haiku-5-5__v1_basic__default"
	v2File = "triage__claude-haiku-5-5__v2_rules__default"
)

// fakeMessage builds an SDK response from literal JSON, like the API would send it.
func fakeMessage(t *testing.T, stop, content string, in, out int) *anthropic.Message {
	t.Helper()
	raw := `{"id":"m","type":"message","role":"assistant","model":"m","content":` + content +
		`,"stop_reason":"` + stop + `","usage":{"input_tokens":` + itoa(in) + `,"output_tokens":` + itoa(out) + `}}`
	var m anthropic.Message
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatal(err)
	}
	return &m
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func textContent(s string) string {
	b, _ := json.Marshal(s)
	return `[{"type":"text","text":` + string(b) + `}]`
}

// recorder is a fake modelCaller: it keeps every request and answers with a valid,
// tiny reply in the shape the request's schema asks for.
type recorder struct {
	mu    sync.Mutex
	calls []anthropic.MessageNewParams
	reply func(p anthropic.MessageNewParams) (*anthropic.Message, error)
}

func (r *recorder) call(t *testing.T) modelCaller {
	return func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error) {
		r.mu.Lock()
		r.calls = append(r.calls, p)
		r.mu.Unlock()
		if r.reply != nil {
			return r.reply(p)
		}
		body := `{"category":"billing","priority":"high","reply":"We are looking into it."}`
		if _, ok := p.OutputConfig.Format.Schema["properties"].(map[string]any)["tone"]; ok {
			body = `{"addresses_issue":{"pass":true,"reason":"ok"},"tone":{"pass":true,"reason":"ok"},` +
				`"no_false_promises":{"pass":true,"reason":"ok"}}`
		}
		return fakeMessage(t, "end_turn", textContent(body), 900, 700), nil
	}
}

func (r *recorder) n() int { r.mu.Lock(); defer r.mu.Unlock(); return len(r.calls) }

// asJSON turns request params into the JSON the SDK would send.
func asJSON(t *testing.T, p anthropic.MessageNewParams) map[string]any {
	t.Helper()
	b := must(json.Marshal(p))
	return field[map[string]any](t, b)
}

// ── 4. Cache keys ───────────────────────────────────────────────────────────

func TestCacheKeyMatchesPython(t *testing.T) {
	vec := field[struct {
		Kind          string  `json:"kind"`
		Model         string  `json:"model"`
		Effort        *string `json:"effort"`
		PromptVersion string  `json:"prompt_version"`
		PromptText    string  `json:"prompt_text"`
		Input         string  `json:"input"`
		Rep           int     `json:"rep"`
		Canonical     string  `json:"canonical"`
		Key           string  `json:"key"`
	}](t, expected(t)["key_vector"])
	if vec.Effort != nil {
		t.Fatal("the key vector is expected to have a null effort")
	}
	req := keyRequest(vec.Kind, vec.Model, "", vec.PromptVersion, vec.PromptText, vec.Input, vec.Rep)
	if got := canonicalJSON(req); got != vec.Canonical {
		t.Errorf("canonical JSON differs from Python's:\n got %s\nwant %s", got, vec.Canonical)
	}
	sum := sha256.Sum256([]byte(vec.Canonical)) // the stored canonical string really hashes to the stored key
	if hex.EncodeToString(sum[:]) != vec.Key {
		t.Errorf("sha256(canonical) = %x, want %s", sum, vec.Key)
	}
	if got := cacheKey(vec.Kind, vec.Model, "", vec.PromptVersion, vec.PromptText, vec.Input, vec.Rep); got != vec.Key {
		t.Errorf("cacheKey = %s, want %s", got, vec.Key)
	}
}

// The real cross-language check: every key Python stored in a fixture file, triage and
// judge, is the key Go computes for the same request.
func TestEveryFixtureKeyMatchesPython(t *testing.T) {
	prompts := must(loadPrompts(filepath.Join(fixtures, "prompts.json")))
	tickets := ticketsByID(t)
	labels := map[string]ReplyLabel{}
	for _, l := range must(loadJSONL[ReplyLabel](filepath.Join(fixtures, "reply_labels.jsonl"))) {
		labels[l.ID] = l
	}
	files := must(filepath.Glob(filepath.Join(fixtures, "responses", "*.jsonl")))
	checked := map[string]int{}
	for _, f := range files {
		for _, l := range must(loadJSONL[Line](f)) {
			c := l.Config
			var key string
			switch c.Kind {
			case "triage":
				key = triageKey(tickets[l.CaseID].Text, c.PromptVersion, c.Model, deref(c.Effort), c.Rep, prompts)
			case "judge":
				lab := labels[l.CaseID]
				key = judgeKey(tickets[lab.TicketID].Text, lab.Reply, c.PromptVersion, c.Model, prompts)
			}
			if key != l.Key {
				t.Errorf("%s %s rep %d: Go key %s, Python stored %s", filepath.Base(f), l.CaseID, c.Rep, key, l.Key)
			}
			checked[c.Kind]++
		}
	}
	if checked["triage"] == 0 || checked["judge"] == 0 {
		t.Fatalf("expected triage and judge fixture lines, checked %v", checked)
	}
}

func TestCanonicalJSONHasNoEscaping(t *testing.T) {
	got := canonicalJSON(map[string]any{"b": "<a&b> é \u2028 \\u2028", "a": nil, "c": map[string]any{"z": 1, "y": "x"}})
	want := "{\"a\":null,\"b\":\"<a&b> é \u2028 \\\\u2028\",\"c\":{\"y\":\"x\",\"z\":1}}"
	if got != want { // Python's ensure_ascii=False writes U+2028 raw; Go escapes it unless we undo that
		t.Errorf("got  %s\nwant %s", got, want)
	}
}

// ── 5 and 9. Graders, report, compare, gate ─────────────────────────────────

func TestScoresMatchExpected(t *testing.T) {
	exp := expected(t)
	type scores struct {
		Category, Priority, Both, Band float64
		Errors, N, Scored              int
	}
	for _, name := range []string{v1File, v2File} {
		want := field[scores](t, exp[name])
		r := must(report(graded(t, name, -1)))
		if r.N != want.N || r.Errors != want.Errors || r.Scored != want.Scored {
			t.Errorf("%s: n/errors/scored = %d/%d/%d, want %d/%d/%d", name, r.N, r.Errors, r.Scored,
				want.N, want.Errors, want.Scored)
		}
		if !near(r.Category, want.Category) || !near(r.Priority, want.Priority) || !near(r.Both, want.Both) ||
			!near(r.Band, want.Band) {
			t.Errorf("%s: report %+v, want %+v", name, r, want)
		}
	}

	// errors are left out of accuracy; a refusal counts as wrong
	v1 := graded(t, v1File, -1)
	if len(v1) != 8 || !near(accuracy(v1, "both_ok"), 3.0/7) {
		t.Errorf("v1 both accuracy %v over %d grades, want 3/7 over 8", accuracy(v1, "both_ok"), len(v1))
	}
	if accuracy(nil, "both_ok") != 0 {
		t.Error("accuracy of nothing should be 0")
	}

	// confusion matrix over the non-error v1 lines
	var pairs [][2]string
	by := ticketsByID(t)
	for _, l := range must(loadJSONL[Line](filepath.Join(fixtures, "responses", v1File+".jsonl"))) {
		if l.failed() {
			continue
		}
		pairs = append(pairs, [2]string{by[l.CaseID].Category, triageOutput(l).Category})
	}
	wantCM := field[map[string]map[string]int](t, exp["v1_basic_confusion_category"])
	if got := confusionMatrix(pairs); !jsonEqual(t, got, wantCM) {
		t.Errorf("confusion matrix %v, want %v", got, wantCM)
	}

	// noise band
	if b := must(noiseBand(16)); !near(b, 0.25) {
		t.Errorf("noiseBand(16) = %v", b)
	}
	if _, err := noiseBand(0); err == nil || !strings.Contains(err.Error(), "no scored cases") {
		t.Errorf("noiseBand(0) should fail with 'no scored cases', got %v", err)
	}

	// compare: one grade per case, rep 0 of v2
	if _, err := compare(graded(t, v2File, -1), graded(t, v2File, 0)); err == nil {
		t.Error("compare should reject duplicate case ids")
	}
	wantC := field[struct {
		A, B, Diff, Band float64
		Fixed, Broken    []string
		Significant      bool
	}](t, exp["compare_v1_v2"])
	c := must(compare(graded(t, v1File, -1), graded(t, v2File, 0)))
	if !near(c.A, wantC.A) || !near(c.B, wantC.B) || !near(c.Diff, wantC.Diff) || !near(c.Band, wantC.Band) ||
		c.Significant != wantC.Significant || !jsonEqual(t, c.Fixed, wantC.Fixed) || !jsonEqual(t, c.Broken, wantC.Broken) {
		t.Errorf("compare %+v, want %+v", c, wantC)
	}
}

func jsonEqual(t *testing.T, a, b any) bool {
	t.Helper()
	ja, jb := must(json.Marshal(a)), must(json.Marshal(b))
	if string(ja) == "null" {
		ja = []byte("[]")
	}
	if string(jb) == "null" {
		jb = []byte("[]")
	}
	return bytes.Equal(ja, jb)
}

func TestGradeOutcomes(t *testing.T) {
	tk := Ticket{ID: "x", Category: "bug", Priority: "high"}
	ok := grade(tk, Line{Output: json.RawMessage(`{"category":"bug","priority":"low","reply":"r"}`), StopReason: ptr("end_turn")})
	if ok != (Grade{CaseID: "x", CategoryOK: true, Outcome: "ok"}) {
		t.Errorf("grade = %+v", ok)
	}
	for _, c := range []struct {
		line    Line
		outcome string
	}{
		{Line{StopReason: ptr("refusal")}, "refusal"},
		{Line{StopReason: ptr("max_tokens")}, "max_tokens"},
		{Line{Error: ptr("boom")}, "error"},
	} {
		g := grade(tk, c.line)
		if g.Outcome != c.outcome || g.CategoryOK || g.PriorityOK || g.BothOK {
			t.Errorf("%s: %+v", c.outcome, g)
		}
	}
}

func fakeGrades(n, right int) []Grade {
	out := make([]Grade, n)
	for i := range out {
		ok := i < right
		out[i] = Grade{CaseID: "c" + itoa(i), CategoryOK: ok, PriorityOK: ok, BothOK: ok, Outcome: "ok"}
	}
	return out
}

func TestGate(t *testing.T) {
	if g := must(gate(fakeGrades(100, 75), fakeGrades(100, 80), 0.7)); !g.Passed || len(g.Reasons) != 0 {
		t.Errorf("a drop inside the band should pass: %+v", g)
	}
	g := must(gate(fakeGrades(100, 75), fakeGrades(100, 90), 0.7))
	if g.Passed || len(g.Reasons) != 1 || !strings.Contains(g.Reasons[0], "baseline") {
		t.Errorf("a drop beyond the band should fail on the baseline: %+v", g)
	}
	g = must(gate(fakeGrades(100, 50), fakeGrades(100, 50), 0.7))
	if g.Passed || !strings.Contains(g.Reasons[0], "minimum") {
		t.Errorf("below the minimum should fail: %+v", g)
	}
}

// ── 6 and 7. Judge and agreement ────────────────────────────────────────────

func TestKappaAndFlipsMatchExpected(t *testing.T) {
	T, F := true, false
	for _, c := range []struct {
		a, b []bool
		want float64
	}{
		{[]bool{T, T, F, F}, []bool{T, T, F, F}, 1},
		{[]bool{T, T, F, F}, []bool{T, F, T, F}, 0},
		{[]bool{T, T, T, F}, []bool{T, T, F, F}, 0.5},
		{[]bool{T, T, T, T}, []bool{T, T, T, T}, 1}, // pe == 1 and po == 1
		{[]bool{T, T, T, T}, []bool{F, F, F, F}, 0}, // pe == 0, po == 0
	} {
		if k := must(cohenKappa(c.a, c.b)); !near(k, c.want) {
			t.Errorf("cohenKappa(%v, %v) = %v, want %v", c.a, c.b, k, c.want)
		}
	}
	if _, err := cohenKappa(nil, nil); err == nil {
		t.Error("cohenKappa of empty lists should fail")
	}

	exp := expected(t)
	wantAgree := field[map[string]float64](t, exp["agreement"])
	wantKappa := field[map[string]float64](t, exp["kappa"])
	wantFlips := field[map[string]int](t, exp["position_flips"])
	prompts := must(loadPrompts(filepath.Join(fixtures, "prompts.json")))
	tickets := ticketsByID(t)
	labels := must(loadJSONL[ReplyLabel](filepath.Join(fixtures, "reply_labels.jsonl")))
	byOrder := map[string][]map[string]*bool{}
	for _, order := range []string{"before", "after"} {
		for _, l := range labels {
			line := must(judge(context.Background(), tickets[l.TicketID].Text, l.Reply, order,
				callOpts{mode: "replay", root: fixtures, prompts: &prompts}, l.ID))
			byOrder[order] = append(byOrder[order], verdicts(line))
		}
	}
	for _, rid := range rubricIDs {
		var before, after []*bool
		var human []bool
		for i, l := range labels {
			before, after = append(before, byOrder["before"][i][rid]), append(after, byOrder["after"][i][rid])
			human = append(human, l.Labels[rid])
		}
		if a := must(agreement(after, human, rid)); !near(a, wantAgree[rid]) {
			t.Errorf("%s agreement %v, want %v", rid, a, wantAgree[rid])
		}
		if k := must(cohenKappa(asBools(after), human)); !near(k, wantKappa[rid]) {
			t.Errorf("%s kappa %v, want %v", rid, k, wantKappa[rid])
		}
		if f := must(positionFlips(before, after)); f != wantFlips[rid] {
			t.Errorf("%s flips %d, want %d", rid, f, wantFlips[rid])
		}
	}
}

func TestVerdictsOfNullOutput(t *testing.T) {
	for rid, v := range verdicts(Line{}) {
		if v != nil {
			t.Errorf("%s should be nil for a line with no output", rid)
		}
	}
	nilV, yes := []*bool{nil}, []*bool{ptr(true)}
	if n := must(positionFlips(nilV, yes)); n != 1 {
		t.Error("nil vs true is a flip")
	}
	if a := must(agreement(nilV, []bool{false}, "x")); a != 0 {
		t.Error("an ungraded reply counts as a disagreement")
	}
}

func TestJudgeOrderChangesMessageAndKey(t *testing.T) {
	prompts := must(loadPrompts(filepath.Join(fixtures, "prompts.json")))
	rec := &recorder{}
	o := callOpts{mode: "live", caller: rec.call(t), root: t.TempDir(), prompts: &prompts}
	before := must(judge(context.Background(), "TICKET TEXT", "REPLY TEXT", "before", o, "c1"))
	after := must(judge(context.Background(), "TICKET TEXT", "REPLY TEXT", "after", o, "c1"))
	if before.Key == after.Key {
		t.Error("the two orders must have different keys")
	}
	msg := func(i int) string {
		return asJSON(t, rec.calls[i])["messages"].([]any)[0].(map[string]any)["content"].([]any)[0].(map[string]any)["text"].(string)
	}
	b, a := msg(0), msg(1)
	if !(strings.Index(b, "addresses_issue") < strings.Index(b, "REPLY TEXT")) ||
		!(strings.Index(a, "addresses_issue") > strings.Index(a, "REPLY TEXT")) {
		t.Errorf("rubric position is wrong:\nbefore %q\nafter %q", b, a)
	}
	sys := rec.calls[0].System[0].Text
	if strings.Contains(sys, "{rubric}") || !strings.Contains(sys, "addresses_issue") {
		t.Errorf("judge system prompt should have the rubric filled in: %q", sys)
	}
	if _, err := judge(context.Background(), "t", "r", "sideways", o, "c1"); err == nil {
		t.Error("an unknown order should fail")
	}
}

// ── 3 and 4. Requests and saved responses ───────────────────────────────────

func TestNoSamplingParams(t *testing.T) {
	prompts := must(loadPrompts(filepath.Join(fixtures, "prompts.json")))
	tk := must(loadTickets(filepath.Join(fixtures, "tickets.jsonl")))[0]
	rec := &recorder{}
	o := callOpts{model: taskModel, mode: "live", caller: rec.call(t), root: t.TempDir(), prompts: &prompts}
	must(triage(context.Background(), tk, "v1_basic", o))
	o.effort = "low"
	must(triage(context.Background(), tk, "v1_basic", o))
	o.model = "" // the judge has its own default model
	must(judge(context.Background(), tk.Text, "a reply", "after", o, tk.ID))
	for i, p := range rec.calls {
		j := asJSON(t, p)
		for _, k := range []string{"temperature", "top_p", "top_k", "tool_choice"} {
			if _, ok := j[k]; ok {
				t.Errorf("call %d sends %s", i, k)
			}
		}
		oc := j["output_config"].(map[string]any)
		if oc["format"].(map[string]any)["type"] != "json_schema" {
			t.Errorf("call %d: output_config.format = %v", i, oc["format"])
		}
		_, hasEffort := oc["effort"]
		if wantEffort := i == 1; hasEffort != wantEffort {
			t.Errorf("call %d: effort present = %v, want %v (%v)", i, hasEffort, wantEffort, oc)
		}
	}
	j := asJSON(t, rec.calls[0])
	if j["system"].([]any)[0].(map[string]any)["text"] != prompts.Triage["v1_basic"] ||
		j["messages"].([]any)[0].(map[string]any)["content"].([]any)[0].(map[string]any)["text"] != tk.Text {
		t.Errorf("triage request has the wrong system prompt or message: %v", j)
	}
	if asJSON(t, rec.calls[2])["model"] != judgeModel {
		t.Error("the judge must use the judge model")
	}
}

func testCall(t *testing.T, mode, root string, caller modelCaller) (Line, error) {
	t.Helper()
	cfg := Config{Kind: "triage", Model: "m", PromptVersion: "v1", Rep: 0}
	key := cacheKey("triage", "m", "", "v1", "p", "hello", 0)
	return callModel(context.Background(), anthropic.MessageNewParams{Model: "m"}, key, cfg, "t1", mode, caller, root)
}

func TestReplayMissMessage(t *testing.T) {
	_, err := testCall(t, "replay", t.TempDir(), nil)
	var miss *ReplayMiss
	if !errors.As(err, &miss) {
		t.Fatalf("want a ReplayMiss, got %v", err)
	}
	for _, s := range []string{"no saved response", "record", "t1", "triage"} {
		if !strings.Contains(err.Error(), s) {
			t.Errorf("miss message %q should mention %q", err, s)
		}
	}
	// the CLI reports a miss on stderr and exits 1, with no panic
	var out, errOut bytes.Buffer
	if code := cli([]string{"run", "--data", fixtures, "--model", "claude-sonnet-5-5"}, nil, &out, &errOut); code != 1 {
		t.Errorf("exit %d, want 1", code)
	}
	if !strings.Contains(errOut.String(), "no saved response") {
		t.Errorf("stderr %q", errOut.String())
	}
}

func TestRecordThenReplay(t *testing.T) {
	root := t.TempDir()
	good := `{"category":"bug","priority":"high","reply":"Sorry about that."}`
	fail := &recorder{reply: func(anthropic.MessageNewParams) (*anthropic.Message, error) {
		return nil, errors.New("connection reset")
	}}
	line := must(testCall(t, "record", root, fail.call(t)))
	if line.Error == nil || line.hasOutput() {
		t.Fatalf("an API error is saved as an error line: %+v", line)
	}
	// record re-records a key whose latest line errored, and replay then returns the newest line
	ok := &recorder{reply: func(anthropic.MessageNewParams) (*anthropic.Message, error) {
		return fakeMessage(t, "end_turn", textContent(good), 12, 7), nil
	}}
	line = must(testCall(t, "record", root, ok.call(t)))
	if line.InputTokens != 12 || line.OutputTokens != 7 || line.Error != nil {
		t.Errorf("recorded line %+v", line)
	}
	again := must(testCall(t, "record", root, ok.call(t)))
	if ok.n() != 1 || !again.skipped {
		t.Errorf("record must skip a key already recorded without error (calls %d)", ok.n())
	}
	replayed := must(testCall(t, "replay", root, nil))
	if string(replayed.Output) != good || replayed.Error != nil {
		t.Errorf("replay should return the latest line: %+v", replayed)
	}
	path := responsePath("triage", "m", "v1", "", root)
	if n := len(must(loadJSONL[Line](path))); n != 2 {
		t.Errorf("file has %d lines, want 2 (error, then success)", n)
	}
	// live does not save
	live := t.TempDir()
	must(testCall(t, "live", live, ok.call(t)))
	if files, _ := filepath.Glob(filepath.Join(live, "responses", "*")); len(files) != 0 {
		t.Errorf("live saved %v", files)
	}
}

func TestUnusableResponsesAreSavedAsErrorsOrNull(t *testing.T) {
	for _, c := range []struct {
		stop, content, wantErr string
	}{
		{"end_turn", `[]`, "no text block"},
		{"end_turn", textContent("not json"), "invalid JSON"},
		{"refusal", textContent(`{"category":`), ""},
		{"max_tokens", textContent(`{"category":`), ""},
	} {
		root := t.TempDir()
		rec := &recorder{reply: func(anthropic.MessageNewParams) (*anthropic.Message, error) {
			return fakeMessage(t, c.stop, c.content, 12, 7), nil
		}}
		line := must(testCall(t, "record", root, rec.call(t)))
		if line.hasOutput() || line.InputTokens != 12 || *line.StopReason != c.stop {
			t.Errorf("%s/%s: %+v", c.stop, c.wantErr, line)
		}
		if c.wantErr != "" && (line.Error == nil || !strings.Contains(*line.Error, c.wantErr)) {
			t.Errorf("error %v, want %q", line.Error, c.wantErr)
		}
		replayed := must(testCall(t, "replay", root, nil))
		if !jsonEqual(t, replayed, line) {
			t.Errorf("replay %+v != recorded %+v", replayed, line)
		}
	}
}

func TestSavedLineShapeMatchesContract(t *testing.T) {
	root := t.TempDir()
	rec := &recorder{reply: func(anthropic.MessageNewParams) (*anthropic.Message, error) {
		return fakeMessage(t, "refusal", textContent(""), 3, 4), nil
	}}
	must(testCall(t, "record", root, rec.call(t)))
	b := must(os.ReadFile(responsePath("triage", "m", "v1", "", root)))
	m := field[map[string]any](t, b)
	keys := []string{"key", "config", "case_id", "output", "stop_reason", "input_tokens", "output_tokens", "latency_ms", "error"}
	if len(m) != len(keys) {
		t.Errorf("line has keys %v", m)
	}
	for _, k := range keys {
		if _, ok := m[k]; !ok {
			t.Errorf("line misses %s", k)
		}
	}
	if m["output"] != nil || m["config"].(map[string]any)["effort"] != nil || m["input_tokens"] != 3.0 {
		t.Errorf("line %s", b)
	}
	if !strings.HasPrefix(string(b), `{"key":`) || bytes.Count(b, []byte("\n")) != 1 {
		t.Errorf("one JSON object per line: %q", b)
	}
}

// ── 8. Runner ───────────────────────────────────────────────────────────────

func fakeLine(caseID string, inputTokens int) Line {
	return Line{Config: Config{Model: "claude-haiku-5-5"}, CaseID: caseID, InputTokens: inputTokens}
}

func TestRunKeepsOrder(t *testing.T) {
	cases := []int{0, 1, 2, 3, 4, 5}
	lines := must(run(cases, func(c, rep int) (Line, error) {
		time.Sleep(time.Duration(5-c) * 20 * time.Millisecond) // the first case finishes last
		return fakeLine(itoa(c)+"/"+itoa(rep), 1), nil
	}, 2, 6, noCap))
	var got []string
	for _, l := range lines {
		got = append(got, l.CaseID)
	}
	want := "0/0 0/1 1/0 1/1 2/0 2/1 3/0 3/1 4/0 4/1 5/0 5/1"
	if strings.Join(got, " ") != want {
		t.Errorf("order %v, want %s", got, want)
	}
}

func TestRunStopsAtMaxCost(t *testing.T) {
	var mu sync.Mutex
	var started []int
	_, err := run([]int{0, 1, 2, 3, 4, 5, 6, 7, 8, 9}, func(c, rep int) (Line, error) {
		mu.Lock()
		started = append(started, c)
		mu.Unlock()
		return fakeLine(itoa(c), 1_000_000), nil // $0.10 each on Haiku
	}, 1, 1, 0.25)
	var limit *CostLimit
	if !errors.As(err, &limit) {
		t.Fatalf("want CostLimit, got %v", err)
	}
	var kept []string
	for _, l := range limit.Lines {
		kept = append(kept, l.CaseID)
	}
	if strings.Join(kept, ",") != "0,1,2" || !near(limit.Spent, 0.30) || len(started) != 3 {
		t.Errorf("kept %v, spent %v, started %v", kept, limit.Spent, started)
	}
	lines := must(run([]int{1, 2}, func(c, rep int) (Line, error) { return fakeLine(itoa(c), 1), nil }, 1, 8, 1.0))
	if len(lines) != 2 {
		t.Errorf("a run within the cap returns everything, got %d", len(lines))
	}
	// a skipped (already recorded) line costs nothing now
	skipped := fakeLine("s", 1_000_000_000)
	skipped.skipped = true
	if _, err := run([]int{1}, func(int, int) (Line, error) { return skipped, nil }, 1, 1, 0.0001); err != nil {
		t.Errorf("a skipped line must not count against the cap: %v", err)
	}
}

func TestCost(t *testing.T) {
	if c := must(callCost("claude-haiku-5-5", 1_000_000, 1_000_000)); !near(c, 0.60) {
		t.Errorf("haiku cost %v", c)
	}
	if _, err := callCost("claude-unknown", 1, 1); err == nil {
		t.Error("an unknown model has no price")
	}
	in, out := estTokens["triage"][0], estTokens["triage"][1]
	if c := must(estimateCost("triage", "claude-sonnet-5-5", 10)); !near(c, 10*(float64(in)*2+float64(out)*10)/1e6) {
		t.Errorf("estimate %v", c)
	}
	if c := must(worstCaseCost("triage", "claude-sonnet-5-5", 2)); !near(c, 2*(float64(in)*2+maxTokens*10)/1e6) {
		t.Errorf("worst case %v", c)
	}
}

// ── 10. Command line ────────────────────────────────────────────────────────

func runCLI(t *testing.T, caller modelCaller, args ...string) (int, string, string) {
	t.Helper()
	var out, errOut bytes.Buffer
	code := cli(args, caller, &out, &errOut)
	return code, out.String(), errOut.String()
}

func TestCLIReplay(t *testing.T) {
	code, out, _ := runCLI(t, nil, "run", "--data", fixtures)
	if code != 0 || !strings.Contains(out, "v2_rules") || !strings.Contains(out, "band") {
		t.Errorf("run: %d %q", code, out)
	}
	code, out, _ = runCLI(t, nil, "--data", fixtures, "run", "--prompt", "v1_basic", "--split", "test")
	if code != 0 || !strings.Contains(out, "v1_basic") {
		t.Errorf("--data before the command: %d %q", code, out)
	}
	code, out, _ = runCLI(t, nil, "compare", "--data", fixtures, "--prompt", "v1_basic", "--vs", "v2_rules")
	if code != 0 || !strings.Contains(out, "diff") || !strings.Contains(out, "fixed f001, f004") {
		t.Errorf("compare: %d %q", code, out)
	}
	code, out, _ = runCLI(t, nil, "agreement", "--data", fixtures)
	if code != 0 || !strings.Contains(out, "kappa") || !strings.Contains(out, "flips") {
		t.Errorf("agreement: %d %q", code, out)
	}
	code, _, errOut := runCLI(t, nil, "judge", "--data", fixtures, "--prompt", "v2_rules")
	if code != 1 || !strings.Contains(errOut, "no saved response") {
		t.Errorf("judge on fixture triage replies should miss: %d %q", code, errOut)
	}
	if code, _, _ := runCLI(t, nil, "gate", "--data", fixtures, "--prompt", "v2_rules", "--baseline", "v1_basic", "--minimum", "0.0"); code != 0 {
		t.Errorf("gate should pass, exit %d", code)
	}
	if code, _, _ := runCLI(t, nil, "gate", "--data", fixtures, "--prompt", "v1_basic", "--baseline", "v2_rules", "--minimum", "1.1"); code != 1 {
		t.Errorf("gate should fail, exit %d", code)
	}
	if code, _, _ := runCLI(t, nil, "run", "--data", fixtures, "--prompt", "nope"); code != 1 {
		t.Errorf("an unknown prompt should exit 1, got %d", code)
	}
}

func copyFixtures(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "data")
	if err := os.CopyFS(root, os.DirFS(fixtures)); err != nil {
		t.Fatal(err)
	}
	return root
}

func TestPaidModesNeedConfirmation(t *testing.T) {
	root := copyFixtures(t)
	before := must(filepath.Glob(filepath.Join(root, "responses", "*")))
	rec := &recorder{}
	isTerminal = func() bool { return false }
	defer func() { isTerminal = stdinIsTerminal }()
	for _, args := range [][]string{
		{"run", "--mode", "record", "--model", "claude-sonnet-5-5"},
		{"run", "--mode", "live"},
		{"judge", "--mode", "live"},
	} {
		code, out, _ := runCLI(t, rec.call(t), append(args, "--data", root)...)
		if code != 1 || !strings.Contains(out, "Estimated cost") || !strings.Contains(out, "Nothing was called") ||
			!strings.Contains(out, "worst case $") || !strings.Contains(out, "Cap: $10.00.") {
			t.Errorf("%v: %d %q", args, code, out)
		}
	}
	after := must(filepath.Glob(filepath.Join(root, "responses", "*")))
	if rec.n() != 0 || len(after) != len(before) {
		t.Errorf("nothing may be called or saved without a yes (calls %d)", rec.n())
	}

	// an interactive y approves, an n refuses
	isTerminal = func() bool { return true }
	readAnswer = func() string { return "y\n" }
	defer func() { readAnswer = readStdinLine }()
	if code, _, _ := runCLI(t, rec.call(t), "run", "--mode", "live", "--data", root); code != 0 || rec.n() != 4 {
		t.Errorf("interactive y: exit %d, calls %d (want 4 dev tickets)", code, rec.n())
	}
	readAnswer = func() string { return "n\n" }
	if code, _, _ := runCLI(t, rec.call(t), "run", "--mode", "live", "--data", root); code != 1 || rec.n() != 4 {
		t.Errorf("interactive n: exit %d, calls %d", code, rec.n())
	}

	// --yes: the judge grades the 4 saved dev replies
	code, out, _ := runCLI(t, rec.call(t), "judge", "--mode", "live", "--yes", "--data", root, "--prompt", "v2_rules")
	if code != 0 || rec.n() != 8 || !strings.Contains(out, "addresses_issue") {
		t.Errorf("judge --yes: %d calls %d %q", code, rec.n(), out)
	}
	// the cap stops a record run and keeps what finished
	code, out, _ = runCLI(t, rec.call(t), "run", "--mode", "record", "--yes", "--max-cost", "0.0001", "--workers", "1",
		"--model", "claude-opus-5-5", "--data", root)
	if code != 2 || !strings.Contains(out, "max-cost") || !strings.Contains(out, "saved") || rec.n() != 9 {
		t.Errorf("capped record: %d calls %d %q", code, rec.n(), out)
	}
}

func TestDataDirIsResolvedFromTheSource(t *testing.T) {
	if _, err := os.Stat(filepath.Join(dataDir, "tickets.jsonl")); err != nil {
		t.Errorf("dataDir %s should be llms/data: %v", dataDir, err)
	}
	if !filepath.IsAbs(dataDir) {
		t.Errorf("dataDir %s should be absolute", dataDir)
	}
}

func TestLoadJSONLNamesFileAndLine(t *testing.T) {
	p := filepath.Join(t.TempDir(), "broken.jsonl")
	os.WriteFile(p, []byte("{\"id\": \"a\"}\n{bad\n"), 0o644)
	_, err := loadJSONL[Ticket](p)
	if err == nil || !strings.Contains(err.Error(), "broken.jsonl:2:") {
		t.Errorf("err %v", err)
	}
}

// routeAPIToRecorder sends any call that would reach the real API (a nil caller falls back
// to apiCaller) to the recorder instead, so the test counts it and nothing leaves the machine.
func routeAPIToRecorder(t *testing.T, rec *recorder) {
	apiOnce = sync.Once{}
	apiOnce.Do(func() { apiCall = rec.call(t) })
	t.Cleanup(func() { apiOnce, apiCall = sync.Once{}, nil })
}

// defaultMode is only the CLI's default --mode: editing it must never turn a replay-only
// lookup into a paid call, and every paid path still asks first.
func TestEditingDefaultModeNeverMakesUnconfirmedCalls(t *testing.T) {
	root := copyFixtures(t)
	isTerminal = func() bool { return false }
	defer func() { isTerminal = stdinIsTerminal }()
	defer func(m string) { defaultMode = m }(defaultMode)
	for _, mode := range []string{"live", "record"} {
		defaultMode = mode
		rec := &recorder{}
		routeAPIToRecorder(t, rec)
		for _, args := range [][]string{
			{"run"}, {"run", "--mode", "replay"},
			{"compare", "--prompt", "v1_basic", "--vs", "v2_rules"},
			{"judge"}, {"judge", "--mode", "replay"},
			{"agreement"},
			{"gate", "--prompt", "v2_rules", "--baseline", "v1_basic", "--minimum", "0.0"},
		} {
			runCLI(t, rec.call(t), append(args, "--data", root)...)
			if rec.n() != 0 {
				t.Fatalf("defaultMode %s, %v: %d calls, want 0", mode, args, rec.n())
			}
		}
	}
}

func TestLibraryCallsDefaultToReplay(t *testing.T) {
	defer func(m string) { defaultMode = m }(defaultMode)
	defaultMode = "live"
	rec := &recorder{}
	routeAPIToRecorder(t, rec)
	ctx := context.Background()
	prompts := must(loadPrompts(filepath.Join(fixtures, "prompts.json")))
	tk := must(loadTickets(filepath.Join(fixtures, "tickets.jsonl")))[0]
	if _, err := triage(ctx, tk, "v2_rules", callOpts{caller: rec.call(t), root: fixtures, prompts: &prompts}); err != nil {
		t.Fatal(err)
	}
	l := must(loadJSONL[ReplyLabel](filepath.Join(fixtures, "reply_labels.jsonl")))[0]
	judge(ctx, tk.Text, l.Reply, "after", callOpts{caller: rec.call(t), root: fixtures, prompts: &prompts}, l.ID) // a hit or a miss, never a call
	if rec.n() != 0 {
		t.Errorf("zero-value callOpts must replay; got %d calls", rec.n())
	}
}

func TestJudgePaidModesRefuseWithoutYes(t *testing.T) {
	root := copyFixtures(t)
	isTerminal = func() bool { return false }
	defer func() { isTerminal = stdinIsTerminal }()
	rec := &recorder{}
	for _, mode := range []string{"record", "live"} {
		code, out, _ := runCLI(t, rec.call(t), "judge", "--mode", mode, "--data", root)
		if code != 1 || !strings.Contains(out, "Nothing was called") || rec.n() != 0 {
			t.Errorf("judge --mode %s: exit %d, calls %d, %q", mode, code, rec.n(), out)
		}
	}
}

func TestLiveCapMessageSaysNothingIsSaved(t *testing.T) {
	rec := &recorder{}
	code, out, _ := runCLI(t, rec.call(t), "run", "--mode", "live", "--yes", "--max-cost", "0.0001", "--workers", "1",
		"--model", "claude-opus-5-5", "--data", copyFixtures(t))
	if code != 2 || !strings.Contains(out, "Live mode saves nothing.") || strings.Contains(out, "Finished calls are saved") {
		t.Errorf("capped live run: %d %q", code, out)
	}
}

func TestCLIErrorsAreOneLine(t *testing.T) {
	for _, c := range []struct {
		args []string
		want string
	}{
		{[]string{"run", "--prompt", "nope"}, "error: unknown prompt 'nope'; choose from v1_basic, v2_rules, v3_examples"},
		{[]string{"run", "--mode", "live", "--yes", "--model", "claude-nope"}, `error: no price for model "claude-nope"`},
	} {
		code, _, errOut := runCLI(t, (&recorder{}).call(t), append(c.args, "--data", fixtures)...)
		if code != 1 || strings.TrimSpace(errOut) != c.want {
			t.Errorf("%v: exit %d, stderr %q, want %q", c.args, code, errOut, c.want)
		}
	}
}

func TestMaxCostMustBeFiniteAndPositive(t *testing.T) {
	for _, bad := range []string{"nan", "inf", "0", "-1"} {
		var out, errOut bytes.Buffer
		code := cli([]string{"run", "--data", fixtures, "--max-cost=" + bad}, nil, &out, &errOut)
		if code != 2 || !strings.Contains(errOut.String(), "--max-cost must be a finite number above 0") {
			t.Fatalf("%s: code %d\n%s", bad, code, errOut.String())
		}
	}
}
