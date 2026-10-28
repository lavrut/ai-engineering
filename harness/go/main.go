// mini-harness — the same agent harness as harness.py, written in Go.
//
// Run (the key setup is in lesson 2):
//
//	cd harness/go && go run .
//
// Every task costs money: the REPL prints an estimate and asks y/N first (section 9).
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
)

// ── 1. Configuration ────────────────────────────────────────────────────────
const (
	model         = "claude-opus-5-5"
	maxTokens     = 16000  // cap on a single model response
	contextBudget = 150000 // input tokens before we roll over to a summary
	useFallbacks  = true   // retry on a fallback model if a safety classifier declines
	toolTimeout   = 30 * time.Second
	maxCost       = 10.00 // default --max-cost: the most one REPL session may spend, in dollars
	typicalCalls  = 4     // for the estimate: model calls in a typical task,
	typicalIn     = 4000  // input tokens per call
	typicalOut    = 400   // and output tokens per call
	charsPerToken = 3     // sizes a request before it is sent; low, so the guess runs high
)

// pricePerMTok is the model's dollars per million input / output tokens (PRICES in llms/python/triage_lab.py).
var pricePerMTok = [2]float64{4.00, 20.00}

var maxTurns = 25 // hard stop for a runaway loop (a var so tests can lower it)

var (
	workspace, _ = filepath.Abs("./workspace")
	traceFile    = "trace.jsonl"
	systemPrompt = "You are a careful coding assistant working inside " + workspace +
		".\nLook at files before you change them. Keep your final answer short."
	client = anthropic.NewClient(option.WithMaxRetries(4)) // SDK retries 429 / 5xx / network errors
	stdin  = bufio.NewReader(os.Stdin)
)

// ── 2. Observability: every event flows through one function ────────────────
var traceMu sync.Mutex

func emit(event string, data map[string]any) {
	traceMu.Lock() // tools run on several goroutines at once
	defer traceMu.Unlock()
	if data == nil {
		data = map[string]any{}
	}
	data["ts"], data["event"] = time.Now().UnixMilli(), event
	f, err := os.OpenFile(traceFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	json.NewEncoder(f).Encode(data)
}

// ── 3. Tool registry ────────────────────────────────────────────────────────
type Tool struct {
	Name          string
	Description   string
	Properties    map[string]any
	Required      []string
	NeedsApproval bool
	Run           func(ctx context.Context, input json.RawMessage) (string, error)
}

// Param is the part the model sees. Run never leaves the harness.
func (t Tool) Param() anthropic.ToolUnionParam {
	return anthropic.ToolUnionParam{OfTool: &anthropic.ToolParam{
		Name:        t.Name,
		Description: anthropic.String(t.Description),
		InputSchema: anthropic.ToolInputSchemaParam{Properties: t.Properties, Required: t.Required},
	}}
}

var registry = map[string]Tool{}

func register(t Tool) { registry[t.Name] = t }

// safePath is the sandbox rule: tools may only touch files inside workspace.
func safePath(rel string) (string, error) {
	if rel == "" { // a missing "path" field decodes as ""; Python raises TypeError for it
		return "", errors.New("path is required")
	}
	if filepath.IsAbs(rel) { // Join would quietly re-root "/etc/passwd" inside the workspace
		return "", fmt.Errorf("%s is outside the workspace", rel)
	}
	root, err := resolve(workspace)
	if err != nil {
		return "", err
	}
	p, err := resolve(filepath.Join(root, rel)) // Join also cleans away ".."
	if err != nil {
		return "", err
	}
	if p != root && !strings.HasPrefix(p, root+string(os.PathSeparator)) {
		return "", fmt.Errorf("%s is outside the workspace", rel)
	}
	return p, nil
}

// resolve follows symlinks like Python's Path.resolve(), so a link inside the
// workspace can't point a tool outside it. The file may not exist yet (write_file
// creates it), so it resolves the deepest part that exists and keeps the rest.
func resolve(p string) (string, error) {
	rest := ""
	for {
		real, err := filepath.EvalSymlinks(p)
		if err == nil {
			return filepath.Join(real, rest), nil
		}
		if !errors.Is(err, fs.ErrNotExist) {
			return "", err
		}
		if _, err := os.Lstat(p); err == nil { // p exists, so it is a link to nothing
			return "", fmt.Errorf("%s is a broken symlink", filepath.Base(p))
		}
		parent := filepath.Dir(p)
		if parent == p {
			return filepath.Join(p, rest), nil
		}
		rest = filepath.Join(filepath.Base(p), rest)
		p = parent
	}
}

func str(desc string) map[string]any { return map[string]any{"type": "string", "description": desc} }

func init() {
	register(Tool{
		Name: "list_files", Description: "List the files in a workspace directory.",
		Properties: map[string]any{"path": str("Directory relative to the workspace, e.g. '.'")},
		Required:   []string{"path"},
		Run: func(ctx context.Context, raw json.RawMessage) (string, error) {
			var in struct{ Path string }
			if err := json.Unmarshal(raw, &in); err != nil {
				return "", err
			}
			dir, err := safePath(in.Path)
			if err != nil {
				return "", err
			}
			entries, err := os.ReadDir(dir)
			if err != nil {
				return "", err
			}
			names := make([]string, 0, len(entries))
			for _, e := range entries {
				name := e.Name()
				if e.IsDir() {
					name += "/"
				}
				names = append(names, name)
			}
			sort.Strings(names)
			if len(names) == 0 {
				return "(empty directory)", nil
			}
			return strings.Join(names, "\n"), nil
		},
	})
	register(Tool{
		Name: "read_file", Description: "Read a UTF-8 text file from the workspace.",
		Properties: map[string]any{"path": str("File path relative to the workspace")},
		Required:   []string{"path"},
		Run: func(ctx context.Context, raw json.RawMessage) (string, error) {
			var in struct{ Path string }
			if err := json.Unmarshal(raw, &in); err != nil {
				return "", err
			}
			p, err := safePath(in.Path)
			if err != nil {
				return "", err
			}
			b, err := os.ReadFile(p)
			if err != nil {
				return "", err
			}
			if len(b) > 20000 { // cap what one tool can push into context
				b = b[:20000]
			}
			return string(b), nil
		},
	})
	register(Tool{
		Name: "write_file", Description: "Create or overwrite a text file in the workspace.",
		Properties: map[string]any{
			"path":    str("File path relative to the workspace"),
			"content": str("The full new file contents"),
		},
		Required:      []string{"path", "content"},
		NeedsApproval: true,
		Run: func(ctx context.Context, raw json.RawMessage) (string, error) {
			var in struct{ Path, Content string }
			if err := json.Unmarshal(raw, &in); err != nil {
				return "", err
			}
			p, err := safePath(in.Path)
			if err != nil {
				return "", err
			}
			if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
				return "", err
			}
			if err := os.WriteFile(p, []byte(in.Content), 0o644); err != nil {
				return "", err
			}
			return fmt.Sprintf("Wrote %d characters to %s", len(in.Content), in.Path), nil
		},
	})
}

// modelCaller and approver are the two seams: tests swap in fakes, main wires the real ones.
type (
	modelCaller func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error)
	approver    func(name, args string) bool
)

// ── 4. Permission gate ──────────────────────────────────────────────────────
func approve(name, args string) bool {
	if len(args) > 200 {
		args = args[:200]
	}
	fmt.Printf("\n  ! The agent wants to run %s(%s)\n    Allow? [y/N] ", name, args)
	line, _ := stdin.ReadString('\n') // end of input reads as "", which is no
	ok := strings.TrimSpace(strings.ToLower(line)) == "y"
	emit("permission", map[string]any{"tool": name, "allowed": ok})
	return ok
}

// ── 5. Tool executor ────────────────────────────────────────────────────────
type call struct {
	id, name string
	input    json.RawMessage
}

func runOne(ctx context.Context, c call, allowed bool) anthropic.ContentBlockParamUnion {
	start := time.Now()
	preview := string(c.input)
	if len(preview) > 80 {
		preview = preview[:80]
	}
	fmt.Printf("  -> %s(%s)\n", c.name, preview)

	content, isError := "", true
	t, known := registry[c.name]
	switch {
	case !known:
		content = "Unknown tool: " + c.name
	case !allowed:
		content = "The user denied this action."
	default:
		ctx, cancel := context.WithTimeout(ctx, toolTimeout) // every tool gets a deadline
		defer cancel()
		out, err := runTool(ctx, t, c.input)
		if err != nil { // a tool failure is information for the model, not a crash
			content = err.Error()
		} else {
			content, isError = out, false
		}
	}
	emit("tool_result", map[string]any{"tool": c.name, "is_error": isError,
		"ms": time.Since(start).Milliseconds()})
	return anthropic.NewToolResultBlock(c.id, content, isError)
}

// runTool turns a panicking tool into an error, so one bad tool can't crash the harness.
func runTool(ctx context.Context, t Tool, input json.RawMessage) (out string, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("panic: %v", r)
		}
	}()
	return t.Run(ctx, input)
}

func executeTools(ctx context.Context, calls []call, ok approver) []anthropic.ContentBlockParamUnion {
	// Ask for approvals one at a time (stdin is shared) ...
	allowed := make([]bool, len(calls))
	for i, c := range calls {
		t, known := registry[c.name]
		allowed[i] = !known || !t.NeedsApproval || ok(c.name, string(c.input))
	}
	// ... then run every approved call on its own goroutine.
	results := make([]anthropic.ContentBlockParamUnion, len(calls))
	var wg sync.WaitGroup
	for i, c := range calls {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = runOne(ctx, c, allowed[i]) // index keeps the original order
		}()
	}
	wg.Wait()
	return results
}

// ── 6. Model call ───────────────────────────────────────────────────────────
func toolParams() []anthropic.ToolUnionParam {
	out := []anthropic.ToolUnionParam{}
	for _, name := range []string{"list_files", "read_file", "write_file"} {
		out = append(out, registry[name].Param())
	}
	return out
}

func callModel(ctx context.Context, params anthropic.MessageNewParams) (*anthropic.Message, error) {
	params.Model = model
	params.System = []anthropic.TextBlockParam{{Text: systemPrompt}}
	params.Tools = toolParams()
	if params.MaxTokens == 0 {
		params.MaxTokens = maxTokens
	}
	opts := []option.RequestOption{}
	if useFallbacks {
		opts = append(opts,
			option.WithHeader("anthropic-beta", "server-side-fallback-2026-07-01"),
			option.WithJSONSet("fallbacks", "default"))
	}
	emit("model_request", map[string]any{"messages": len(params.Messages)})
	resp, err := client.Messages.New(ctx, params, opts...)
	if err != nil {
		return nil, err
	}
	emit("model_response", map[string]any{"stop_reason": resp.StopReason,
		"input_tokens": resp.Usage.InputTokens, "output_tokens": resp.Usage.OutputTokens})
	return resp, nil
}

func textOf(m *anthropic.Message) string {
	var sb strings.Builder
	for _, block := range m.Content {
		if tb, ok := block.AsAny().(anthropic.TextBlock); ok {
			sb.WriteString(tb.Text)
		}
	}
	return sb.String()
}

// ── 7. Context management: roll over to a summary ───────────────────────────
// We never edit old turns in place: current models reject edited history,
// so the harness stays append-only and starts a fresh conversation instead.
func compact(ctx context.Context, messages []anthropic.MessageParam, model modelCaller) ([]anthropic.MessageParam, error) {
	emit("compact", map[string]any{"messages": len(messages)})
	ask := anthropic.NewUserMessage(anthropic.NewTextBlock("Summarize this session for a fresh start: " +
		"the goal, what you did, files touched, and what is left to do."))
	summary, err := model(ctx, anthropic.MessageNewParams{
		Messages:   append(messages, ask),
		MaxTokens:  4000,
		ToolChoice: anthropic.ToolChoiceUnionParam{OfNone: &anthropic.ToolChoiceNoneParam{}},
	})
	if err != nil {
		return nil, err
	}
	return []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock(
		"Summary of earlier work:\n" + textOf(summary) + "\n\nContinue the task."))}, nil
}

// ── 8. The agent loop ───────────────────────────────────────────────────────
func runAgent(ctx context.Context, task string, model modelCaller, ok approver) (string, error) {
	messages := []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock(task))}
	emit("session_start", map[string]any{"task": task})

	for turn := 1; turn <= maxTurns; turn++ {
		resp, err := model(ctx, anthropic.MessageNewParams{Messages: messages})
		if err != nil {
			return "", err
		}
		messages = append(messages, resp.ToParam())

		if resp.StopReason == anthropic.StopReasonToolUse {
			var calls []call
			for _, block := range resp.Content {
				if tu, ok := block.AsAny().(anthropic.ToolUseBlock); ok {
					calls = append(calls, call{tu.ID, tu.Name, json.RawMessage(tu.JSON.Input.Raw())})
				}
			}
			// All results go back in ONE user message, matched by tool_use_id.
			messages = append(messages, anthropic.NewUserMessage(executeTools(ctx, calls, ok)...))
			if resp.Usage.InputTokens > contextBudget {
				if messages, err = compact(ctx, messages, model); err != nil {
					return "", err
				}
			}
			continue
		}

		emit("session_end", map[string]any{"turns": turn, "stop_reason": resp.StopReason})
		switch resp.StopReason {
		case anthropic.StopReasonMaxTokens:
			return textOf(resp) + "\n[stopped: the response hit max_tokens]", nil
		case "refusal":
			return "[the model declined this request]", nil
		}
		return textOf(resp), nil // end_turn: the model is done
	}
	emit("session_end", map[string]any{"turns": maxTurns, "stop_reason": "max_turns"})
	return "[stopped: reached maxTurns without finishing]", nil
}

// ── 9. Entry point: a tiny REPL ─────────────────────────────────────────────
func dollars(in, out int64) float64 {
	return (float64(in)*pricePerMTok[0] + float64(out)*pricePerMTok[1]) / 1e6
}

// costLimit: the next model call could take the session past --max-cost, so it was not made.
type costLimit struct{ spent, worst, cap float64 }

func (e *costLimit) Error() string {
	return fmt.Sprintf("spent $%.4f; the next call could cost up to $%.4f, over --max-cost %.2f", e.spent, e.worst, e.cap)
}

type ledger struct{ cap, spent float64 }

// metered is the money gate on every model call: stop if spent + this call's worst case would pass the cap.
func metered(call modelCaller, l *ledger) modelCaller {
	return func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error) {
		sent, _ := json.Marshal([]any{systemPrompt, toolParams(), p.Messages})
		out := p.MaxTokens
		if out == 0 {
			out = maxTokens
		}
		worst := dollars(int64(len(sent)/charsPerToken), out)
		if l.spent+worst > l.cap {
			return nil, &costLimit{l.spent, worst, l.cap}
		}
		resp, err := call(ctx, p)
		if err != nil {
			return nil, err
		}
		l.spent += dollars(resp.Usage.InputTokens, resp.Usage.OutputTokens)
		return resp, nil
	}
}

// commas writes 150000 as 150,000, like Python's {:,}.
func commas(n int) string {
	s := fmt.Sprint(n)
	for i := len(s) - 3; i > 0; i -= 3 {
		s = s[:i] + "," + s[i:]
	}
	return s
}

func estimateLines(l *ledger) []string {
	typical := typicalCalls * dollars(typicalIn, typicalOut)
	worst := float64(maxTurns) * dollars(contextBudget, maxTokens)
	return []string{
		fmt.Sprintf("Estimate: typical $%.2f a task (%d calls of ~%s input and ~%s output tokens); worst case $%.2f "+
			"(MAX_TURNS %d calls of %s input and %s output tokens, plus any compaction summaries), at $%.2f / $%.2f "+
			"per million tokens.", typical, typicalCalls, commas(typicalIn), commas(typicalOut), worst, maxTurns,
			commas(contextBudget), commas(maxTokens), pricePerMTok[0], pricePerMTok[1]),
		fmt.Sprintf("Spent $%.4f this session; --max-cost stops it before $%.2f.", l.spent, l.cap),
	}
}

// isTerminal reports whether standard input is a terminal (a var so tests can swap it).
var isTerminal = func() bool {
	fi, err := os.Stdin.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}

// confirmed: y on a terminal, or --yes for a scripted run you approved. Anything else is no.
func confirmed(yes bool) bool {
	if yes {
		return true
	}
	if !isTerminal() {
		fmt.Println("Not run: no terminal to ask. Nothing was spent.")
		return false
	}
	fmt.Print("Run it? [y/N] ")
	line, _ := stdin.ReadString('\n')
	if strings.TrimSpace(strings.ToLower(line)) == "y" {
		return true
	}
	fmt.Println("Not run. Nothing was spent.")
	return false
}

func repl(args []string, call modelCaller) int {
	fs := flag.NewFlagSet("mini-harness", flag.ContinueOnError)
	budget := fs.Float64("max-cost", maxCost, "dollars this session may spend")
	yes := fs.Bool("yes", false, "scripted runs only: approve every task without asking")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if os.Getenv("ANTHROPIC_API_KEY") == "" {
		fmt.Println("Set ANTHROPIC_API_KEY first (lesson 2). Nothing was called.")
		return 1
	}
	if err := os.MkdirAll(workspace, 0o755); err != nil {
		fmt.Println("Error:", err)
		return 1
	}
	l := &ledger{cap: *budget}
	fmt.Printf("mini-harness · %s · workspace %s\nType a task, or 'exit'.\n", model, workspace)
	for {
		fmt.Print("\nyou > ")
		line, err := stdin.ReadString('\n')
		if err != nil {
			return 0
		}
		task := strings.TrimSpace(line)
		if task == "exit" || task == "quit" {
			return 0
		}
		if task == "" {
			continue
		}
		fmt.Println(strings.Join(estimateLines(l), "\n"))
		if !confirmed(*yes) {
			continue
		}
		before := l.spent
		answer, err := runAgent(context.Background(), task, metered(call, l), approve)
		var apiErr *anthropic.Error
		var limit *costLimit
		switch {
		case errors.As(err, &limit):
			fmt.Println("Stopped:", limit)
		case errors.As(err, &apiErr) && apiErr.StatusCode == 429:
			fmt.Println("Rate limited even after retries. Wait a minute and try again.")
		case errors.As(err, &apiErr):
			fmt.Printf("API error %d: %s\n", apiErr.StatusCode, apiErr.Error())
		case err != nil:
			fmt.Println("Error:", err)
		default:
			fmt.Println("\nagent >", answer)
		}
		fmt.Printf("  [$%.4f this task, $%.4f this session]\n", l.spent-before, l.spent)
	}
}

func main() { os.Exit(repl(os.Args[1:], callModel)) }
