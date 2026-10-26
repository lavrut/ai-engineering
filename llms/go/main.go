// triage_lab — the same evaluation lab as triage_lab.py, written in Go.
//
// The feature is support-ticket triage for "Lumen", a fictional cloud notes app:
// the model reads a ticket and returns a category, a priority and a reply draft.
// The lab is everything around that call — the dataset, the prompts, saved
// responses, graders, an LLM judge, a runner and a report. It reads and writes the
// same saved-response files as the Python version, with the same keys and scores.
//
// Run (from llms/go):
//
//	go run . run                        # replay saved responses: free, no API key
//	go run . compare --prompt v1_basic --vs v2_rules
//	go run . judge --prompt v2_rules    # judge the saved replies
//	go run . agreement                  # judge vs. the human labels
//	go run . gate --prompt v3_examples --baseline v2_rules
//
//	export ANTHROPIC_API_KEY=sk-ant-... # only needed to record or run live
//	go run . run --mode live --model claude-sonnet-5-5 --effort high
//
// Add --data ../data/fixtures to use the tiny test data instead.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
)

// ── 1. Configuration ────────────────────────────────────────────────────────
const (
	taskModel   = "claude-haiku-5-5" // the model that does the triage
	judgeModel  = "claude-opus-5-5"  // the model that grades the replies
	defaultMode = "replay"           // replay (saved responses) | record | live
)

// Dollars per million tokens: {input, output}. Used to estimate cost before any paid run.
var prices = map[string][2]float64{
	"claude-haiku-5-5":  {0.10, 0.50},
	"claude-sonnet-5-5": {2.00, 10.00},
	"claude-opus-5-5":   {4.00, 20.00},
}

// Resolved from this source file, not the working directory, so it works from anywhere
// (Python does the same with __file__). A data root holds tickets.jsonl, prompts.json,
// reply_labels.jsonl and responses/; llms/data/fixtures is another root.
var dataDir = func() string {
	_, file, _, _ := runtime.Caller(0) // .../llms/go/main.go
	return filepath.Join(filepath.Dir(filepath.Dir(file)), "data")
}()

var (
	categories = []string{"billing", "bug", "account", "how_to", "feature_request", "outage"}
	priorities = []string{"low", "normal", "high", "urgent"}
)

// ── 2. Dataset ──────────────────────────────────────────────────────────────
type Ticket struct {
	ID       string   `json:"id"`
	Text     string   `json:"text"`
	Category string   `json:"category"`
	Priority string   `json:"priority"`
	Tags     []string `json:"tags"`
	Split    string   `json:"split"` // "dev" or "test"
}

// ReplyLabel is a human verdict on one reply, per rubric criterion.
type ReplyLabel struct {
	ID       string          `json:"id"`
	TicketID string          `json:"ticket_id"`
	Reply    string          `json:"reply"`
	Labels   map[string]bool `json:"labels"`
}

// Prompts holds the triage prompt versions, the judge prompt and the judge rubric.
type Prompts struct {
	Triage map[string]string `json:"triage"`
	Judge  string            `json:"judge"`
	Rubric []struct {
		ID   string `json:"id"`
		Text string `json:"text"`
	} `json:"rubric"`
}

// loadJSONL reads one JSON object per line. A bad line names its file and line number.
func loadJSONL[T any](path string) ([]T, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var rows []T
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 16*1024*1024) // a saved reply can be a long line
	for n := 1; sc.Scan(); n++ {
		if strings.TrimSpace(sc.Text()) == "" {
			continue
		}
		var row T
		if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
			return nil, fmt.Errorf("%s:%d: %v", filepath.Base(path), n, err)
		}
		rows = append(rows, row)
	}
	return rows, sc.Err()
}

func loadTickets(path string) ([]Ticket, error) { return loadJSONL[Ticket](path) }

// split keeps one split: tune on "dev"; touch "test" only to report a final number.
func split(tickets []Ticket, name string) []Ticket {
	var out []Ticket
	for _, t := range tickets {
		if t.Split == name {
			out = append(out, t)
		}
	}
	return out
}

func loadPrompts(path string) (Prompts, error) {
	var p Prompts
	b, err := os.ReadFile(path)
	if err == nil {
		err = json.Unmarshal(b, &p)
	}
	return p, err
}

// ── 3. The task ─────────────────────────────────────────────────────────────
// The model must answer in this shape. Structured output enforces it, so the
// reply is always parseable JSON with an allowed category and priority.
var triageSchema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"category": map[string]any{"type": "string", "enum": categories},
		"priority": map[string]any{"type": "string", "enum": priorities},
		"reply":    map[string]any{"type": "string"},
	},
	"required":             []string{"category", "priority", "reply"},
	"additionalProperties": false,
}

// The judge returns a verdict and a one-sentence reason for every rubric criterion.
var rubricIDs = []string{"addresses_issue", "tone", "no_false_promises"}

var judgeSchema = func() map[string]any {
	verdict := map[string]any{
		"type":                 "object",
		"properties":           map[string]any{"pass": map[string]any{"type": "boolean"}, "reason": map[string]any{"type": "string"}},
		"required":             []string{"pass", "reason"},
		"additionalProperties": false,
	}
	props := map[string]any{}
	for _, id := range rubricIDs {
		props[id] = verdict
	}
	return map[string]any{"type": "object", "properties": props, "required": rubricIDs, "additionalProperties": false}
}()

// ── 4. Model call with saved responses ──────────────────────────────────────
// Every model call is identified by a key. In replay mode the key is looked up
// in a saved-response file, so the lab runs free, fast and the same every time.

// ReplayMiss: replay found no saved response. It never falls back to a paid call.
type ReplayMiss struct{ msg string }

func (e *ReplayMiss) Error() string { return e.msg }

// keyRequest is everything that shapes the answer. An empty effort is JSON null.
func keyRequest(kind, model, effort, promptVersion, promptText string, input any, rep int) map[string]any {
	return map[string]any{"kind": kind, "model": model, "effort": nullable(effort),
		"prompt_version": promptVersion, "prompt_text": promptText, "input": input, "rep": rep}
}

// canonicalJSON matches Python's json.dumps(sort_keys=True, separators=(",", ":"),
// ensure_ascii=False): Go sorts map keys, SetEscapeHTML(false) keeps < > & as they are,
// and UTF-8 is written raw. Go still escapes U+2028/U+2029, which Python does not, so
// those two are put back.
func canonicalJSON(v any) string {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		panic(err) // only maps, strings and ints go in: this cannot fail
	}
	s := strings.TrimSuffix(buf.String(), "\n")
	if !strings.Contains(s, `\u202`) {
		return s
	}
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		switch {
		case s[i] != '\\':
			b.WriteByte(s[i])
		case strings.HasPrefix(s[i:], "\\u2028"), strings.HasPrefix(s[i:], "\\u2029"):
			r, _ := strconv.ParseUint(s[i+2:i+6], 16, 32)
			b.WriteRune(rune(r))
			i += 5
		default: // any other escape: copy it whole, so `\\u2028` stays as written
			b.WriteString(s[i : i+2])
			i++
		}
	}
	return b.String()
}

// cacheKey is the sha256 of the canonical JSON of the request. Change the prompt text
// or the rep and the key changes, so stale answers never match.
func cacheKey(kind, model, effort, promptVersion, promptText string, input any, rep int) string {
	sum := sha256.Sum256([]byte(canonicalJSON(keyRequest(kind, model, effort, promptVersion, promptText, input, rep))))
	return hex.EncodeToString(sum[:])
}

// responsePath: one file per configuration inside <root>/responses; all reps share the file.
func responsePath(kind, model, promptVersion, effort, root string) string {
	if effort == "" {
		effort = "default"
	}
	return filepath.Join(root, "responses", fmt.Sprintf("%s__%s__%s__%s.jsonl", kind, model, promptVersion, effort))
}

const maxTokens = 2000 // the most output one call may use; the worst-case cost assumes it all

// Config and Line are the saved-response contract, field for field, so either
// language can replay what the other recorded. null in the file is a nil pointer here.
type Config struct {
	Kind          string  `json:"kind"`
	Model         string  `json:"model"`
	Effort        *string `json:"effort"`
	PromptVersion string  `json:"prompt_version"`
	Rep           int     `json:"rep"`
}

type Line struct {
	Key          string          `json:"key"`
	Config       Config          `json:"config"`
	CaseID       string          `json:"case_id"`
	Output       json.RawMessage `json:"output"` // null on refusal, max_tokens or error
	StopReason   *string         `json:"stop_reason"`
	InputTokens  int             `json:"input_tokens"`
	OutputTokens int             `json:"output_tokens"`
	LatencyMs    int             `json:"latency_ms"`
	Error        *string         `json:"error"`
	skipped      bool            // not saved: record found it already done, so it cost nothing now
}

func (l Line) failed() bool    { return l.Error != nil && *l.Error != "" }
func (l Line) hasOutput() bool { return len(l.Output) > 0 && string(l.Output) != "null" }

func nullable(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

func ptr[T any](v T) *T { return &v }

// latestLine is the last saved line for key (the latest recording wins), if any.
func latestLine(path, key string) (Line, bool, error) {
	lines, err := loadJSONL[Line](path)
	if errors.Is(err, fs.ErrNotExist) {
		return Line{}, false, nil
	}
	if err != nil {
		return Line{}, false, err
	}
	for i := len(lines) - 1; i >= 0; i-- {
		if lines[i].Key == key {
			return lines[i], true, nil
		}
	}
	return Line{}, false, nil
}

// modelCaller is the seam: tests pass a fake, a paid run uses the real API.
type modelCaller func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error)

var (
	apiOnce sync.Once
	apiCall modelCaller
)

// apiCaller creates the client only when a paid mode needs it, so replay never needs a key.
func apiCaller() modelCaller {
	apiOnce.Do(func() {
		client := anthropic.NewClient(option.WithMaxRetries(4)) // SDK retries 429 / 5xx / network errors
		apiCall = func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error) {
			return client.Messages.New(ctx, p)
		}
	})
	return apiCall
}

var writeMu sync.Mutex // runner goroutines append to the same file; one write at a time

// callModel returns one saved-response line (root is a data root).
// replay: read it from disk (the latest recording of the key wins). record: call the API
// and append it, skipping keys whose latest line has no error. live: call, do not save.
func callModel(ctx context.Context, params anthropic.MessageNewParams, key string, cfg Config, caseID, mode string,
	caller modelCaller, root string) (Line, error) {
	path := responsePath(cfg.Kind, cfg.Model, cfg.PromptVersion, deref(cfg.Effort), root)
	switch mode {
	case "replay":
		line, ok, err := latestLine(path, key)
		if err != nil || ok {
			return line, err
		}
		return Line{}, &ReplayMiss{fmt.Sprintf("no saved response for %s / %s / %s / case %s (rep %d). "+
			"Record it: python triage_lab.py record ...", cfg.Kind, cfg.Model, cfg.PromptVersion, caseID, cfg.Rep)}
	case "record":
		saved, ok, err := latestLine(path, key)
		if err != nil {
			return Line{}, err
		}
		if ok && !saved.failed() { // resume: never pay twice for the same request
			saved.skipped = true
			return saved, nil
		}
	case "live":
	default:
		return Line{}, fmt.Errorf("unknown mode %q", mode)
	}

	if caller == nil {
		caller = apiCaller()
	}
	line := Line{Key: key, Config: cfg, CaseID: caseID}
	start := time.Now()
	resp, err := caller(ctx, params)
	if err != nil {
		line.Error = ptr(err.Error())
	} else {
		line.StopReason = ptr(string(resp.StopReason))
		line.InputTokens, line.OutputTokens = int(resp.Usage.InputTokens), int(resp.Usage.OutputTokens)
		// A refusal or a cut-off reply has no usable JSON; leave output null.
		if resp.StopReason != anthropic.StopReasonRefusal && resp.StopReason != anthropic.StopReasonMaxTokens {
			line.Output, line.Error = parseOutput(resp)
		}
	}
	line.LatencyMs = int(math.Round(float64(time.Since(start).Microseconds()) / 1000))

	if mode == "record" {
		if err := appendLine(path, line); err != nil {
			return line, err
		}
	}
	return line, nil
}

// parseOutput reads the JSON answer from the first text block. A missing block or bad
// JSON is returned as an error message, so the line is saved and the call is not paid twice.
func parseOutput(resp *anthropic.Message) (json.RawMessage, *string) {
	i := slices.IndexFunc(resp.Content, func(b anthropic.ContentBlockUnion) bool { return b.Type == "text" })
	if i < 0 {
		return nil, ptr("no text block in response")
	}
	text := []byte(resp.Content[i].Text)
	var parsed any
	if err := json.Unmarshal(text, &parsed); err != nil {
		return nil, ptr("invalid JSON: " + err.Error())
	}
	var compact bytes.Buffer // one line per saved response
	json.Compact(&compact, text)
	return compact.Bytes(), nil
}

func appendLine(path string, line Line) error {
	writeMu.Lock()
	defer writeMu.Unlock()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	enc := json.NewEncoder(f) // Encode ends the line with "\n"
	enc.SetEscapeHTML(false)
	return enc.Encode(line)
}

func triageKey(ticketText, variant, model, effort string, rep int, p Prompts) string {
	return cacheKey("triage", model, effort, variant, p.Triage[variant], ticketText, rep)
}

// callOpts are the optional arguments of triage and judge. Zero values mean: the
// default model, replay, rep 0, no effort, llms/data, prompts read from the root.
type callOpts struct {
	model, effort string
	rep           int
	mode          string
	caller        modelCaller
	root          string
	prompts       *Prompts
}

func (o callOpts) withDefaults(model string) (callOpts, Prompts, error) {
	if o.model == "" {
		o.model = model
	}
	if o.mode == "" {
		o.mode = defaultMode
	}
	if o.root == "" {
		o.root = dataDir
	}
	if o.prompts != nil {
		return o, *o.prompts, nil
	}
	p, err := loadPrompts(filepath.Join(o.root, "prompts.json"))
	return o, p, err
}

// triage triages one ticket with one prompt version.
func triage(ctx context.Context, t Ticket, variant string, o callOpts) (Line, error) {
	o, prompts, err := o.withDefaults(taskModel)
	if err != nil {
		return Line{}, err
	}
	promptText, ok := prompts.Triage[variant]
	if !ok {
		return Line{}, fmt.Errorf("unknown prompt %q", variant)
	}
	params := anthropic.MessageNewParams{ // no temperature/top_p/top_k, no tool_choice
		Model:     anthropic.Model(o.model),
		MaxTokens: maxTokens,
		System:    []anthropic.TextBlockParam{{Text: promptText}},
		Messages:  []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock(t.Text))},
		OutputConfig: anthropic.OutputConfigParam{
			Format: anthropic.JSONOutputFormatParam{Schema: triageSchema}, // type defaults to json_schema
			Effort: anthropic.OutputConfigEffort(o.effort),                // "" is omitted: no effort sent
		},
	}
	key := triageKey(t.Text, variant, o.model, o.effort, o.rep, prompts)
	cfg := Config{Kind: "triage", Model: o.model, Effort: nullable(o.effort), PromptVersion: variant, Rep: o.rep}
	return callModel(ctx, params, key, cfg, t.ID, o.mode, o.caller, o.root)
}

// ── 5. Code graders ─────────────────────────────────────────────────────────
// Category and priority have one right answer, so plain code grades them: exact,
// free and the same every time. Reach for a judge only where code cannot decide.
type Grade struct {
	CaseID                         string
	CategoryOK, PriorityOK, BothOK bool
	Outcome                        string // ok | refusal | max_tokens | error
}

type triageAnswer struct{ Category, Priority, Reply string }

func triageOutput(l Line) triageAnswer {
	var a triageAnswer
	if l.hasOutput() {
		json.Unmarshal(l.Output, &a)
	}
	return a
}

// grade grades one saved line against its ticket. A line with no usable output is never correct.
func grade(t Ticket, l Line) Grade {
	outcome := "ok"
	if l.failed() {
		outcome = "error" // the call failed: the model was never graded
	} else if s := deref(l.StopReason); s == "refusal" || s == "max_tokens" {
		outcome = s // the model answered, but not with a usable result
	}
	out := triageOutput(l)
	cat := outcome == "ok" && out.Category == t.Category
	pri := outcome == "ok" && out.Priority == t.Priority
	return Grade{CaseID: t.ID, CategoryOK: cat, PriorityOK: pri, BothOK: cat && pri, Outcome: outcome}
}

// accuracy is the share of graded cases that are correct on field ("category_ok",
// "priority_ok" or "both_ok"). A refusal or cut-off counts as wrong; an error says
// nothing about the model, so it is left out.
func accuracy(grades []Grade, field string) float64 {
	right, counted := 0, 0
	for _, g := range grades {
		if g.Outcome == "error" {
			continue
		}
		counted++
		if map[string]bool{"category_ok": g.CategoryOK, "priority_ok": g.PriorityOK, "both_ok": g.BothOK}[field] {
			right++
		}
	}
	if counted == 0 {
		return 0
	}
	return float64(right) / float64(counted)
}

// confusionMatrix counts {expected, predicted} pairs: matrix[expected][predicted]. No answer is "(none)".
func confusionMatrix(pairs [][2]string) map[string]map[string]int {
	matrix := map[string]map[string]int{}
	for _, p := range pairs {
		expected, predicted := p[0], p[1]
		if predicted == "" {
			predicted = "(none)"
		}
		if matrix[expected] == nil {
			matrix[expected] = map[string]int{}
		}
		matrix[expected][predicted]++
	}
	return matrix
}

// ── 6. LLM judge ────────────────────────────────────────────────────────────
// Some qualities, like tone, have no exact answer a script can check. A stronger model
// reads the ticket and the reply and grades each rubric item with a reason.
func rubricText(p Prompts) string {
	lines := make([]string, len(p.Rubric))
	for i, item := range p.Rubric {
		lines[i] = item.ID + ": " + item.Text
	}
	return strings.Join(lines, "\n")
}

func judgeSystem(p Prompts) string { return strings.ReplaceAll(p.Judge, "{rubric}", rubricText(p)) }

// judgeKey: the order is the prompt version, so a saved verdict only matches the order that produced it.
func judgeKey(ticketText, reply, order, model string, p Prompts) string {
	return cacheKey("judge", model, "", order, judgeSystem(p), map[string]any{"ticket": ticketText, "reply": reply}, 0)
}

// judge grades one reply against the rubric. order is where the rubric sits in the user
// message: "before" the reply or "after" it. The two orders are different requests with
// different keys, so you can measure whether position changes the verdicts.
func judge(ctx context.Context, ticketText, reply, order string, o callOpts, caseID string) (Line, error) {
	if order != "before" && order != "after" {
		return Line{}, fmt.Errorf("order must be 'before' or 'after', not %q", order)
	}
	o, prompts, err := o.withDefaults(judgeModel)
	if err != nil {
		return Line{}, err
	}
	ticketPart, replyPart, rubricPart := "Ticket:\n"+ticketText, "Reply:\n"+reply, "Rubric:\n"+rubricText(prompts)
	parts := []string{ticketPart, replyPart, rubricPart}
	if order == "before" {
		parts = []string{ticketPart, rubricPart, replyPart}
	}
	params := anthropic.MessageNewParams{
		Model:        anthropic.Model(o.model),
		MaxTokens:    maxTokens,
		System:       []anthropic.TextBlockParam{{Text: judgeSystem(prompts)}},
		Messages:     []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock(strings.Join(parts, "\n\n")))},
		OutputConfig: anthropic.OutputConfigParam{Format: anthropic.JSONOutputFormatParam{Schema: judgeSchema}},
	}
	key := judgeKey(ticketText, reply, order, o.model, prompts)
	cfg := Config{Kind: "judge", Model: o.model, PromptVersion: order, Rep: 0}
	return callModel(ctx, params, key, cfg, caseID, o.mode, o.caller, o.root)
}

// verdicts is pass/fail per criterion from a saved judge line; nil for all when the line has no output.
func verdicts(l Line) map[string]*bool {
	var out map[string]struct {
		Pass *bool `json:"pass"`
	}
	if l.hasOutput() {
		json.Unmarshal(l.Output, &out)
	}
	v := map[string]*bool{}
	for _, id := range rubricIDs {
		v[id] = out[id].Pass
	}
	return v
}

// ── 7. Judge agreement ──────────────────────────────────────────────────────
// A judge is only useful if it agrees with a careful human. Compare its verdicts with
// hand labels, and check that merely moving the rubric does not change them.

// agreement is the share of replies where the judge and the human gave the same verdict
// on one criterion. A reply the judge could not grade (nil) counts as a disagreement.
func agreement(judgeV []*bool, humanV []bool, criterion string) (float64, error) {
	if len(judgeV) != len(humanV) || len(judgeV) == 0 {
		if criterion == "" {
			criterion = "criterion"
		}
		return 0, fmt.Errorf("%s: need two equal-length, non-empty verdict lists", criterion)
	}
	same := 0
	for i, j := range judgeV {
		if j != nil && *j == humanV[i] {
			same++
		}
	}
	return float64(same) / float64(len(judgeV)), nil
}

// cohenKappa is agreement beyond chance: (po - pe) / (1 - pe), where po is the observed
// agreement and pe the agreement two raters would reach by luck given how often each says true.
func cohenKappa(a, b []bool) (float64, error) {
	if len(a) != len(b) || len(a) == 0 {
		return 0, errors.New("need two equal-length, non-empty lists")
	}
	n := float64(len(a))
	var same, trueA, trueB float64
	for i := range a {
		if a[i] == b[i] {
			same++
		}
		if a[i] {
			trueA++
		}
		if b[i] {
			trueB++
		}
	}
	po, pa, pb := same/n, trueA/n, trueB/n
	pe := pa*pb + (1-pa)*(1-pb)
	if pe == 1 { // both raters always say the same thing: kappa is undefined
		if po == 1 {
			return 1, nil
		}
		return 0, nil
	}
	return (po - pe) / (1 - pe), nil
}

// asBools treats an ungraded verdict (nil) as false, for kappa.
func asBools(v []*bool) []bool {
	out := make([]bool, len(v))
	for i, x := range v {
		out[i] = x != nil && *x
	}
	return out
}

// positionFlips counts the replies that get a different verdict when only the rubric's position changes.
func positionFlips(before, after []*bool) (int, error) {
	if len(before) != len(after) {
		return 0, errors.New("need two equal-length verdict lists")
	}
	flips := 0
	for i := range before {
		x, y := before[i], after[i]
		if (x == nil) != (y == nil) || (x != nil && *x != *y) {
			flips++
		}
	}
	return flips, nil
}

// ── 8. Runner ───────────────────────────────────────────────────────────────
// Cost first: every paid run prints an estimate before the first call, and stops
// at a cap you set. Cost = tokens x price, so it can be worked out in advance.
// Rough tokens per call, {input, output}: the output includes any thinking.
var estTokens = map[string][2]int{"triage": {900, 700}, "judge": {1400, 900}}

// callCost is the dollars for one call, from the token counts and prices (per million tokens).
func callCost(model string, inputTokens, outputTokens int) (float64, error) {
	p, ok := prices[model]
	if !ok {
		return 0, fmt.Errorf("no price for model %q", model)
	}
	return (float64(inputTokens)*p[0] + float64(outputTokens)*p[1]) / 1_000_000, nil
}

// estimateCost is the dollars for nCalls of one kind, assuming the typical token counts in estTokens.
func estimateCost(kind, model string, nCalls int) (float64, error) {
	c, err := callCost(model, estTokens[kind][0], estTokens[kind][1])
	return float64(nCalls) * c, err
}

// worstCaseCost is the dollars if every call used all of maxTokens for output.
func worstCaseCost(kind, model string, nCalls int) (float64, error) {
	c, err := callCost(model, estTokens[kind][0], maxTokens)
	return float64(nCalls) * c, err
}

// CostLimit: the actual spend passed --max-cost. Lines holds what finished; Spent is the dollars.
type CostLimit struct {
	Lines []Line
	Spent float64
}

func (e *CostLimit) Error() string { return fmt.Sprintf("spent $%.4f, over the limit", e.Spent) }

var noCap = math.Inf(1) // replay is free: no cost limit

// run calls fn(case, rep) for every case and rep, at most `workers` at a time; results keep
// input order (case by case, reps in order) because each goroutine writes its own index.
// Cost is summed from each line's real token usage. Once it passes maxCost no new call
// starts, calls already in flight finish, and CostLimit carries every line that finished,
// so a capped run still saves its work.
func run[C any](cases []C, fn func(c C, rep int) (Line, error), reps, workers int, maxCost float64) ([]Line, error) {
	type job struct {
		c   C
		rep int
	}
	var jobs []job
	for _, c := range cases {
		for rep := 0; rep < reps; rep++ {
			jobs = append(jobs, job{c, rep})
		}
	}
	results, done, errs := make([]Line, len(jobs)), make([]bool, len(jobs)), make([]error, len(jobs))
	var (
		mu             sync.Mutex
		wg             sync.WaitGroup
		spent          float64
		stopped, overC bool
	)
	sem := make(chan struct{}, max(workers, 1)) // a semaphore: one slot per call in flight
	for i, j := range jobs {
		sem <- struct{}{} // wait for a free slot, so calls start in input order
		mu.Lock()
		stop := stopped
		mu.Unlock()
		if stop { // the cap was hit (or a call failed): start nothing new
			<-sem
			break
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer func() { <-sem }() // runs after the unlock below, so the next start sees `stopped`
			line, err := fn(j.c, j.rep)
			mu.Lock()
			defer mu.Unlock()
			if err == nil && !line.skipped { // a resumed (already recorded) line costs nothing now
				var cost float64
				cost, err = callCost(line.Config.Model, line.InputTokens, line.OutputTokens)
				spent += cost
			}
			if err != nil {
				errs[i], stopped = err, true
				return
			}
			results[i], done[i] = line, true
			if spent > maxCost {
				stopped, overC = true, true
			}
		}()
	}
	wg.Wait()
	for _, err := range errs { // the first failure in input order, like Python's pool.map
		if err != nil {
			return nil, err
		}
	}
	var lines []Line
	for i, ok := range done {
		if ok {
			lines = append(lines, results[i])
		}
	}
	if overC {
		return lines, &CostLimit{Lines: lines, Spent: spent}
	}
	return lines, nil
}

// The recording plan (record --plan, estimate_plan, best_prompt) stays in triage_lab.py:
// recording is done once, from Python. Go replays those files and can run live or
// record single configurations with run/judge --mode record.

// ── 9. Report and compare ───────────────────────────────────────────────────
// A score on a small set is noisy. The band is how far a score can move by luck alone,
// so a difference smaller than the band is not evidence of anything.

// noiseBand is the rough noise on an accuracy, 1/sqrt(m): about two standard errors at worst
// (p = 0.5), where m is the number of scored lines (non-error, across all reps). More scored
// lines shrink it.
func noiseBand(m int) (float64, error) {
	if m <= 0 {
		return 0, errors.New("no scored cases")
	}
	return 1 / math.Sqrt(float64(m)), nil
}

// Report holds the scores for one configuration. N = distinct cases (errors included),
// Errors = lines that errored, Scored = lines the accuracies are computed over.
type Report struct {
	N, Errors, Scored        int
	Category, Priority, Both float64
	Band                     float64
}

func scoredLines(grades []Grade) int {
	n := 0
	for _, g := range grades {
		if g.Outcome != "error" {
			n++
		}
	}
	return n
}

// report scores every graded line, all reps together; band = noiseBand(scored).
func report(grades []Grade) (Report, error) {
	ids := map[string]bool{}
	for _, g := range grades {
		ids[g.CaseID] = true
	}
	scored := scoredLines(grades)
	band, err := noiseBand(scored)
	return Report{N: len(ids), Errors: len(grades) - scored, Scored: scored,
		Category: accuracy(grades, "category_ok"), Priority: accuracy(grades, "priority_ok"),
		Both: accuracy(grades, "both_ok"), Band: band}, err
}

type Comparison struct {
	A, B, Diff, Band float64
	Fixed, Broken    []string
	Significant      bool
}

// compare: does B beat A on both_ok? Pass exactly one grade per case (select one rep);
// a duplicate case id is an error. Diff = B - A. Fixed are cases A got wrong and B right,
// Broken the reverse, sorted by case id; an error counts as not correct, and ids present
// on only one side are ignored in the flips. Band = noiseBand of the smaller scored count
// (conservative); Significant is strict: |Diff| > Band.
func compare(gradesA, gradesB []Grade) (Comparison, error) {
	rightA, errA := oneGradePerCase(gradesA)
	rightB, errB := oneGradePerCase(gradesB)
	if err := errors.Join(errA, errB); err != nil {
		return Comparison{}, err
	}
	a, b := accuracy(gradesA, "both_ok"), accuracy(gradesB, "both_ok")
	band, err := noiseBand(min(scoredLines(gradesA), scoredLines(gradesB)))
	if err != nil {
		return Comparison{}, err
	}
	var shared []string
	for id := range rightA {
		if _, ok := rightB[id]; ok {
			shared = append(shared, id)
		}
	}
	sort.Strings(shared)
	c := Comparison{A: a, B: b, Diff: b - a, Band: band, Fixed: []string{}, Broken: []string{},
		Significant: math.Abs(b-a) > band}
	for _, id := range shared {
		if rightB[id] && !rightA[id] {
			c.Fixed = append(c.Fixed, id)
		}
		if rightA[id] && !rightB[id] {
			c.Broken = append(c.Broken, id)
		}
	}
	return c, nil
}

// oneGradePerCase maps case id to both_ok, and fails on a repeated case id.
func oneGradePerCase(grades []Grade) (map[string]bool, error) {
	right := map[string]bool{}
	for _, g := range grades {
		if _, dup := right[g.CaseID]; dup {
			return nil, errors.New("compare needs exactly one grade per case; select a single rep")
		}
		right[g.CaseID] = g.BothOK
	}
	return right, nil
}

type GateResult struct {
	Passed  bool
	Reasons []string
}

// gate is a pass/fail check for CI: fail below the minimum, or below the baseline by more than the noise band.
func gate(grades, baselineGrades []Grade, minimum float64) (GateResult, error) {
	r, err := report(grades) // the band comes from the scored lines
	if err != nil {
		return GateResult{}, err
	}
	base, err := report(baselineGrades)
	if err != nil {
		return GateResult{}, err
	}
	var reasons []string
	if r.Both < minimum {
		reasons = append(reasons, fmt.Sprintf("both %.3f is below the minimum %.3f", r.Both, minimum))
	}
	if r.Both < base.Both-r.Band {
		reasons = append(reasons, fmt.Sprintf("both %.3f is more than the band %.3f below the baseline %.3f",
			r.Both, r.Band, base.Both))
	}
	return GateResult{Passed: len(reasons) == 0, Reasons: reasons}, nil
}

// ── 10. Command line ────────────────────────────────────────────────────────
// Replay is the default and needs no key. Paid modes (record, live) print the
// estimate first and call the API only after a yes.
type options struct {
	data, split, model, effort, mode string
	workers, reps                    int
	maxCost, minimum                 float64
	yes                              bool
	prompt, vs, order, baseline      string
}

func promptName(p Prompts, name string) (string, error) {
	if _, ok := p.Triage[name]; !ok {
		names := make([]string, 0, len(p.Triage))
		for n := range p.Triage {
			names = append(names, n)
		}
		sort.Strings(names)
		return "", fmt.Errorf("unknown prompt '%s'; choose from %s", name, strings.Join(names, ", "))
	}
	return name, nil
}

func selectSplit(tickets []Ticket, name string) []Ticket {
	if name == "all" {
		return tickets
	}
	return split(tickets, name)
}

type costRow struct {
	kind, model, prompt, effort string
	calls                       int
	cost, worst                 float64
}

func newRow(kind, model, prompt, effort string, calls int) (costRow, error) {
	cost, err := estimateCost(kind, model, calls)
	if err != nil {
		return costRow{}, err
	}
	worst, _ := worstCaseCost(kind, model, calls)
	return costRow{kind, model, prompt, effort, calls, cost, worst}, nil
}

// The two terminal seams, swapped in tests.
var (
	isTerminal = stdinIsTerminal
	readAnswer = readStdinLine
)

// stdinIsTerminal is a stand-in for Python's isatty() without an extra module: a terminal
// is a character device, and /dev/null (also one) is ruled out by name.
func stdinIsTerminal() bool {
	fi, err := os.Stdin.Stat()
	if err != nil || fi.Mode()&os.ModeCharDevice == 0 {
		return false
	}
	null, err := os.Stat(os.DevNull)
	return err != nil || !os.SameFile(fi, null)
}

func readStdinLine() string {
	line, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	return line
}

func orDefault(effort string) string {
	if effort == "" {
		return "default"
	}
	return effort
}

// confirm prints the estimate, the worst case and the cap. True only after --yes or an
// interactive y; otherwise nothing is called.
func confirm(w io.Writer, rows []costRow, yes bool, limit float64) bool {
	var typical, worst float64
	for _, r := range rows {
		fmt.Fprintf(w, "  %-7s %-18s %-12s %-8s %4d calls  $%.4f\n", r.kind, r.model, r.prompt, orDefault(r.effort), r.calls, r.cost)
		typical, worst = typical+r.cost, worst+r.worst
	}
	fmt.Fprintf(w, "Estimated cost $%.2f (worst case $%.2f if every reply uses max_tokens). Cap: $%.2f.\n", typical, worst, limit)
	if worst > limit {
		fmt.Fprintf(w, "The worst case is over the cap; the run stops at $%.2f and keeps what finished.\n", limit)
	}
	if yes {
		return true
	}
	answer := ""
	if isTerminal() {
		fmt.Fprint(w, "Make these paid calls? [y/N] ")
		answer = readAnswer()
	}
	if a := strings.ToLower(strings.TrimSpace(answer)); a != "y" && a != "yes" {
		fmt.Fprintln(w, "Not confirmed. Nothing was called. Add --yes to approve.")
		return false
	}
	return true
}

func capFor(mode string, maxCost float64) float64 {
	if mode == "replay" {
		return noCap
	}
	return maxCost
}

// pyFloat prints a float the way Python's str() does for these flags: 10.0, 0.7, 0.0001.
func pyFloat(f float64) string {
	s := strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.Contains(s, ".") {
		s += ".0"
	}
	return s
}

func pct(x float64) string { return fmt.Sprintf("%.1f%%", x*100) }

// gradesFor triages (or replays) every ticket in the split and grades the lines; all reps together.
func gradesFor(ctx context.Context, o options, tickets []Ticket, prompts Prompts, variant, mode string,
	caller modelCaller, reps int) ([]Grade, error) {
	byID := map[string]Ticket{}
	for _, t := range tickets {
		byID[t.ID] = t
	}
	lines, err := run(selectSplit(tickets, o.split), func(t Ticket, rep int) (Line, error) {
		return triage(ctx, t, variant, callOpts{model: o.model, effort: o.effort, rep: rep, mode: mode,
			caller: caller, root: o.data, prompts: &prompts})
	}, reps, o.workers, capFor(mode, o.maxCost))
	if err != nil {
		return nil, err
	}
	grades := make([]Grade, len(lines))
	for i, l := range lines {
		grades[i] = grade(byID[l.CaseID], l)
	}
	return grades, nil
}

// replayGrades grades one prompt version from saved responses, one rep (rep 0).
func replayGrades(ctx context.Context, o options, tickets []Ticket, prompts Prompts, name string) ([]Grade, error) {
	variant, err := promptName(prompts, name)
	if err != nil {
		return nil, err
	}
	return gradesFor(ctx, o, tickets, prompts, variant, "replay", nil, 1)
}

// confirmPaid asks before a paid mode makes calls; replay is free and needs no yes.
func confirmPaid(w io.Writer, o options, kind, model, prompt, effort string, calls int) (bool, error) {
	if o.mode == "replay" {
		return true, nil
	}
	row, err := newRow(kind, model, prompt, effort, calls)
	if err != nil {
		return false, err
	}
	return confirm(w, []costRow{row}, o.yes, o.maxCost), nil
}

func load(o options) (Prompts, []Ticket, error) {
	prompts, err := loadPrompts(filepath.Join(o.data, "prompts.json"))
	if err != nil {
		return prompts, nil, err
	}
	tickets, err := loadTickets(filepath.Join(o.data, "tickets.jsonl"))
	return prompts, tickets, err
}

func stoppedAtCap(w io.Writer, err error, o options) bool {
	var limit *CostLimit
	if errors.As(err, &limit) {
		fmt.Fprintf(w, "Stopped: spent $%.4f, over --max-cost %s. Finished calls are saved.\n", limit.Spent, pyFloat(o.maxCost))
		return true
	}
	return false
}

func cmdRun(ctx context.Context, o options, caller modelCaller, w io.Writer) (int, error) {
	prompts, tickets, err := load(o)
	if err != nil {
		return 1, err
	}
	variant, err := promptName(prompts, o.prompt)
	if err != nil {
		return 1, err
	}
	if ok, err := confirmPaid(w, o, "triage", o.model, variant, o.effort, len(selectSplit(tickets, o.split))*o.reps); !ok {
		return 1, err
	}
	grades, err := gradesFor(ctx, o, tickets, prompts, variant, o.mode, caller, o.reps)
	if stoppedAtCap(w, err, o) {
		return 2, nil
	}
	if err != nil {
		return 1, err
	}
	r, err := report(grades)
	if err != nil {
		return 1, err
	}
	plural := "s"
	if o.reps == 1 {
		plural = ""
	}
	fmt.Fprintf(w, "%s / %s / effort %s / %s split / %d rep%s / %s\n", variant, o.model, orDefault(o.effort), o.split, o.reps, plural, o.mode)
	fmt.Fprintf(w, "  cases %d  scored lines %d  errors %d\n", r.N, r.Scored, r.Errors)
	fmt.Fprintf(w, "  category accuracy  %s\n", pct(r.Category))
	fmt.Fprintf(w, "  priority accuracy  %s\n", pct(r.Priority))
	fmt.Fprintf(w, "  both accuracy      %s   noise band +/-%s\n", pct(r.Both), pct(r.Band))
	return 0, nil
}

func cmdCompare(ctx context.Context, o options, w io.Writer) (int, error) {
	prompts, tickets, err := load(o)
	if err != nil {
		return 1, err
	}
	a, err := replayGrades(ctx, o, tickets, prompts, o.prompt) // one rep each (rep 0)
	if err != nil {
		return 1, err
	}
	b, err := replayGrades(ctx, o, tickets, prompts, o.vs)
	if err != nil {
		return 1, err
	}
	c, err := compare(a, b)
	if err != nil {
		return 1, err
	}
	verdict := "within the noise: not evidence of a difference"
	if c.Significant {
		verdict = "a real difference"
	}
	orDash := func(ids []string) string {
		if len(ids) == 0 {
			return "-"
		}
		return strings.Join(ids, ", ")
	}
	fmt.Fprintf(w, "%s -> %s on the %s split\n", o.prompt, o.vs, o.split)
	fmt.Fprintf(w, "  %s %s   %s %s   diff %+.1f%%   band +/-%s\n", o.prompt, pct(c.A), o.vs, pct(c.B), c.Diff*100, pct(c.Band))
	fmt.Fprintf(w, "  %s\n", verdict)
	fmt.Fprintf(w, "  fixed %s   broken %s\n", orDash(c.Fixed), orDash(c.Broken))
	return 0, nil
}

func cmdJudge(ctx context.Context, o options, caller modelCaller, w io.Writer) (int, error) {
	prompts, tickets, err := load(o)
	if err != nil {
		return 1, err
	}
	variant, err := promptName(prompts, o.prompt)
	if err != nil {
		return 1, err
	}
	cases := selectSplit(tickets, o.split)
	if ok, err := confirmPaid(w, o, "judge", judgeModel, variant, "", len(cases)); !ok {
		return 1, err
	}
	type pair struct{ ticket, reply string }
	replies, ids := map[string]pair{}, []string{}
	for _, t := range cases { // the reply to judge is the saved triage reply
		line, err := triage(ctx, t, variant, callOpts{model: o.model, effort: o.effort, root: o.data, prompts: &prompts})
		if err != nil {
			return 1, err
		}
		if line.hasOutput() {
			replies[t.ID] = pair{t.Text, triageOutput(line).Reply}
			ids = append(ids, t.ID)
		}
	}
	lines, err := run(ids, func(id string, rep int) (Line, error) {
		return judge(ctx, replies[id].ticket, replies[id].reply, o.order,
			callOpts{mode: o.mode, caller: caller, root: o.data, prompts: &prompts}, id)
	}, 1, o.workers, capFor(o.mode, o.maxCost))
	if stoppedAtCap(w, err, o) {
		return 2, nil
	}
	if err != nil {
		return 1, err
	}
	fmt.Fprintf(w, "judge (%s, rubric %s) on %d %s replies, %s split\n", judgeModel, o.order, len(ids), variant, o.split)
	for _, rid := range rubricIDs {
		pass, graded := 0, 0
		for _, l := range lines {
			if v := verdicts(l)[rid]; v != nil {
				graded++
				if *v {
					pass++
				}
			}
		}
		note := ""
		if graded != len(lines) {
			note = fmt.Sprintf("  (%d not graded)", len(lines)-graded)
		}
		fmt.Fprintf(w, "  %-18s pass %d/%d%s\n", rid, pass, graded, note)
	}
	return 0, nil
}

func cmdAgreement(ctx context.Context, o options, w io.Writer) (int, error) {
	prompts, tickets, err := load(o)
	if err != nil {
		return 1, err
	}
	labels, err := loadJSONL[ReplyLabel](filepath.Join(o.data, "reply_labels.jsonl"))
	if err != nil {
		return 1, err
	}
	text := map[string]string{}
	for _, t := range tickets {
		text[t.ID] = t.Text
	}
	byOrder := map[string]map[string][]*bool{} // order -> criterion -> verdict per reply
	for _, order := range []string{"before", "after"} {
		byOrder[order] = map[string][]*bool{}
		for _, l := range labels {
			line, err := judge(ctx, text[l.TicketID], l.Reply, order, callOpts{root: o.data, prompts: &prompts}, l.ID)
			if err != nil {
				return 1, err
			}
			for rid, v := range verdicts(line) {
				byOrder[order][rid] = append(byOrder[order][rid], v)
			}
		}
		fmt.Fprintf(w, "judge vs human, rubric %s the reply (%d replies)\n", order, len(labels))
		for _, rid := range rubricIDs {
			human := make([]bool, len(labels))
			for i, l := range labels {
				human[i] = l.Labels[rid]
			}
			judgeV := byOrder[order][rid]
			a, err := agreement(judgeV, human, rid)
			if err != nil {
				return 1, err
			}
			k, err := cohenKappa(asBools(judgeV), human)
			if err != nil {
				return 1, err
			}
			fmt.Fprintf(w, "  %-18s agreement %s   kappa %.2f\n", rid, pct(a), k)
		}
	}
	fmt.Fprintln(w, "position flips: verdicts that change when only the rubric position changes")
	for _, rid := range rubricIDs {
		n, err := positionFlips(byOrder["before"][rid], byOrder["after"][rid])
		if err != nil {
			return 1, err
		}
		fmt.Fprintf(w, "  %-18s %d of %d\n", rid, n, len(labels))
	}
	return 0, nil
}

func cmdGate(ctx context.Context, o options, w io.Writer) (int, error) {
	prompts, tickets, err := load(o)
	if err != nil {
		return 1, err
	}
	candidate, err := replayGrades(ctx, o, tickets, prompts, o.prompt)
	if err != nil {
		return 1, err
	}
	baseline, err := replayGrades(ctx, o, tickets, prompts, o.baseline)
	if err != nil {
		return 1, err
	}
	result, err := gate(candidate, baseline, o.minimum)
	if err != nil {
		return 1, err
	}
	status := "FAIL"
	if result.Passed {
		status = "PASS"
	}
	fmt.Fprintf(w, "gate %s vs baseline %s (minimum %s): %s\n", o.prompt, o.baseline, pyFloat(o.minimum), status)
	for _, reason := range result.Reasons {
		fmt.Fprintf(w, "  %s\n", reason)
	}
	if result.Passed {
		return 0, nil
	}
	return 1, nil
}

func oneOf(name, value string, choices ...string) error {
	if !slices.Contains(choices, value) {
		return fmt.Errorf("--%s: invalid choice '%s' (choose from %s)", name, value, strings.Join(choices, ", "))
	}
	return nil
}

// cli parses args like triage_lab.py's argparse setup and runs one command. It returns
// the exit code: 0 ok, 1 failed (or not confirmed), 2 stopped at --max-cost or bad usage.
func cli(args []string, caller modelCaller, stdout, stderr io.Writer) int {
	usage := "usage: triage-lab [--data DIR] {run,compare,judge,agreement,gate} [options]"
	top := flag.NewFlagSet("triage-lab", flag.ContinueOnError)
	top.SetOutput(stderr)
	data := top.String("data", dataDir, "data root (default: llms/data)")
	if err := top.Parse(args); err != nil {
		return 2
	}
	if top.NArg() == 0 {
		fmt.Fprintln(stderr, usage)
		return 2
	}
	command := top.Arg(0)
	o := options{}
	sub := flag.NewFlagSet(command, flag.ContinueOnError)
	sub.SetOutput(stderr)
	sub.StringVar(&o.data, "data", *data, "data root (default: llms/data)")
	sub.StringVar(&o.split, "split", "dev", "dev | test | all")
	sub.StringVar(&o.model, "model", taskModel, "the triage model")
	sub.StringVar(&o.effort, "effort", "", "low | medium | high ...; omit to send none")
	sub.IntVar(&o.workers, "workers", 8, "calls in flight at once")
	o.mode, o.reps, o.maxCost = "replay", 1, 10.0
	paid := func() {
		sub.Float64Var(&o.maxCost, "max-cost", 10.0, "stop when the real spend passes this many dollars")
		sub.BoolVar(&o.yes, "yes", false, "approve the printed estimate without asking")
		sub.StringVar(&o.mode, "mode", defaultMode, "replay | record | live")
	}
	switch command {
	case "run":
		paid()
		sub.StringVar(&o.prompt, "prompt", "v2_rules", "prompt version")
		sub.IntVar(&o.reps, "reps", 1, "repeats per ticket")
	case "compare":
		sub.StringVar(&o.prompt, "prompt", "v1_basic", "prompt A")
		sub.StringVar(&o.vs, "vs", "v2_rules", "prompt B")
	case "judge":
		paid()
		sub.StringVar(&o.prompt, "prompt", "v2_rules", "whose replies to judge")
		sub.StringVar(&o.order, "order", "after", "before | after: where the rubric sits")
	case "agreement":
	case "gate":
		sub.StringVar(&o.prompt, "prompt", "v3_examples", "candidate prompt")
		sub.StringVar(&o.baseline, "baseline", "v2_rules", "baseline prompt")
		sub.Float64Var(&o.minimum, "minimum", 0.7, "lowest both accuracy that passes")
	default:
		fmt.Fprintf(stderr, "%s\nunknown command '%s'\n", usage, command)
		return 2
	}
	if err := sub.Parse(top.Args()[1:]); err != nil {
		return 2
	}
	err := oneOf("split", o.split, "dev", "test", "all")
	if err == nil {
		err = oneOf("mode", o.mode, "replay", "record", "live")
	}
	if err == nil && command == "judge" {
		err = oneOf("order", o.order, "before", "after")
	}
	if err == nil && sub.NArg() > 0 {
		err = fmt.Errorf("unrecognized arguments: %s", strings.Join(sub.Args(), " "))
	}
	if err != nil {
		fmt.Fprintf(stderr, "%s\nerror: %v\n", usage, err)
		return 2
	}

	ctx := context.Background()
	var code int
	switch command {
	case "run":
		code, err = cmdRun(ctx, o, caller, stdout)
	case "compare":
		code, err = cmdCompare(ctx, o, stdout)
	case "judge":
		code, err = cmdJudge(ctx, o, caller, stdout)
	case "agreement":
		code, err = cmdAgreement(ctx, o, stdout)
	case "gate":
		code, err = cmdGate(ctx, o, stdout)
	}
	if err != nil { // a replay miss, a bad file or an unknown prompt: say so, no stack trace
		fmt.Fprintf(stderr, "error: %v\n", err)
		return 1
	}
	return code
}

func main() {
	os.Exit(cli(os.Args[1:], nil, os.Stdout, os.Stderr))
}
