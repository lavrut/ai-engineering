// Lumen MCP client (Go): discover, list tools, call tools, read resources, get prompts.
//
//	go run ./client (-stdio | -url URL [-token-file PATH | -token TOKEN]) [-yes | -no] <command> [args]
//	  -stdio  spawn mcp/python/read_server.py (python from $LUMEN_PYTHON, else .venv-mcp, else python3)
//	  -url    talk to a Streamable HTTP server, e.g. http://127.0.0.1:8765/mcp
//	  -token-file  read the OAuth access token for that server from a file (first line, trimmed)
//	  -token       the token itself: works, but warns (a command line is visible in the process list
//	               and shell history)
//	  LUMEN_TOKEN  (environment) the token, when neither flag is given
//	  Precedence: -token-file, then -token, then LUMEN_TOKEN. The token is sent as
//	  `Authorization: Bearer` on every request, never in the URL. Only with -url.
//	  -yes    answer every input-required (elicitation) round "accept" without asking
//	  -no     answer every round "decline" without asking
//	  (neither) ask on the terminal: the prompt goes to stderr, the answer is read from stdin (y/N)
//	Flags go before the command (Go's flag package stops at the first non-flag).
//
// Commands, and the one JSON shape each prints on stdout (sorted keys, 2-space indent, ASCII only,
// no timestamps). The Python client (mcp/python/client.py) prints the same bytes for the same command:
//
//	discover          {"instructions", "prompts": [names], "protocol_version", "resource_templates": [uriTemplates],
//	                   "resources": [uris], "server": name, "tools": [names]}   (lists sorted by name/URI)
//	tools             [{"annotations", "description", "inputSchema", "name", "outputSchema", "title"}, ...] sorted by name.
//	                  Schemas are normalised to their shape (type / properties / required / items /
//	                  additionalProperties), so each SDK's decoration (titles, $defs, defaults) does not show.
//	call NAME JSON    {"structured": ...} or {"error": text}        (shapes of mcp/fixtures/README.md)
//	read URI          {"contents": [{"uri", "mimeType", "json"}]} or {"error": message}
//	prompt NAME JSON  {"contents": [{"role", "text"}]} or {"error": message}
//
// An error result with no text content prints {"error": "<no text>"}. A resource entry whose text is
// not JSON prints {"uri", "mimeType", "text"} instead of "json". Prompt arguments must be JSON strings
// (else exit 1).
//
// Normalisations both clients apply so the output is identical (the Go side does the work here, the
// Python side does the same in mcp/python/client.py):
//  1. Schema decoration is stripped (normaliseSchema): only type / properties / required / items /
//     additionalProperties survive, $ref is resolved, "null" leaves type unions.
//  2. Annotation hints readOnlyHint / idempotentHint are dropped when false (Go's struct cannot say "absent").
//  3. Integral floats print as integers: Go decodes every number to float64 and prints 1.0 as 1, and the
//     Python client converts to match.
package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"unicode/utf16"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// ── 1. Configuration ────────────────────────────────────────────────────────

// schemaShapeKeys are the schema keys that carry shape. Everything else (title, default, $defs,
// description) is SDK decoration.
var schemaShapeKeys = []string{"type", "properties", "required", "items", "additionalProperties"}

// findRepoRoot walks up from dir to the directory that holds mcp/python/read_server.py.
func findRepoRoot(dir string) (string, error) {
	for {
		if _, err := os.Stat(filepath.Join(dir, "mcp", "python", "read_server.py")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("cannot find mcp/python/read_server.py above the working directory")
		}
		dir = parent
	}
}

// stdioCommand builds the command that spawns lumen-read (Python) over stdio.
func stdioCommand() (*exec.Cmd, error) {
	wd, err := os.Getwd()
	if err != nil {
		return nil, err
	}
	root, err := findRepoRoot(wd)
	if err != nil {
		return nil, err
	}
	python := os.Getenv("LUMEN_PYTHON")
	if python == "" {
		python = filepath.Join(root, ".venv-mcp", "bin", "python")
		if _, err := os.Stat(python); err != nil {
			python = "python3"
		}
	}
	return exec.Command(python, filepath.Join(root, "mcp", "python", "read_server.py")), nil
}

// ── 2. Connecting (stdio / HTTP) ────────────────────────────────────────────

// bearer sets `Authorization: Bearer <token>` on every request it carries.
type bearer struct {
	token string
	next  http.RoundTripper
}

func (b bearer) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context()) // a RoundTripper must not modify the caller's request
	r.Header.Set("Authorization", "Bearer "+b.token)
	return b.next.RoundTrip(r)
}

// tokenFlagWarning is printed (to stderr) whenever -token is given. The Python client prints the same line.
const tokenFlagWarning = "client: warning: a token on the command line is visible in the process list and shell " +
	"history; use a token file or LUMEN_TOKEN"

// resolveToken picks the token to send and whether to warn (a token was given on the command line).
// Precedence: -token-file, then -token, then LUMEN_TOKEN. Empty values count as absent.
func resolveToken(tokenFile, token, env string) (string, bool, error) {
	warn := token != ""
	if tokenFile != "" {
		raw, err := os.ReadFile(tokenFile)
		if err != nil {
			return "", warn, fmt.Errorf("token file not readable: %s", tokenFile)
		}
		first, _, _ := strings.Cut(strings.TrimSpace(string(raw)), "\n")
		if first = strings.TrimSpace(first); first == "" {
			return "", warn, fmt.Errorf("token file is empty: %s", tokenFile)
		}
		return first, warn, nil
	}
	if token != "" {
		return token, warn, nil
	}
	return env, warn, nil
}

// newTransport is the one place the transport is built. With a token, the HTTP client sends
// `Authorization: Bearer <token>` on every request; the URL is left exactly as given (a token never
// goes in it). A token with -stdio is refused: it is an HTTP credential.
func newTransport(stdio bool, url, token string) (mcp.Transport, error) {
	if stdio {
		if token != "" {
			return nil, errors.New("a token needs --url / -url")
		}
		cmd, err := stdioCommand()
		if err != nil {
			return nil, err
		}
		return &mcp.CommandTransport{Command: cmd}, nil
	}
	t := &mcp.StreamableClientTransport{Endpoint: url}
	if token != "" {
		t.HTTPClient = &http.Client{Transport: bearer{token, http.DefaultTransport}}
	}
	return t, nil
}

// connect opens a session. Setting ElicitationHandler is what declares the elicitation capability,
// so a server's confirmation step only runs when the client can answer it.
func connect(ctx context.Context, transport mcp.Transport, answer Answerer) (*mcp.ClientSession, error) {
	client := mcp.NewClient(&mcp.Implementation{Name: "lumen-client", Version: "0.1.0"}, &mcp.ClientOptions{
		ElicitationHandler: elicitationHandler(answer),
	})
	return client.Connect(ctx, transport, nil)
}

// ── 3. Discovery ────────────────────────────────────────────────────────────

// normaliseSchema reduces a JSON Schema to its shape: resolve $ref, keep only schemaShapeKeys, drop
// "null" from type unions, and drop boolean additionalProperties. Python and Go print the same result.
func normaliseSchema(schema any, defs map[string]any) any {
	s, ok := schema.(map[string]any)
	if !ok {
		return nil
	}
	if defs == nil {
		defs, _ = s["$defs"].(map[string]any)
	}
	if ref, ok := s["$ref"].(string); ok {
		return normaliseSchema(defs[ref[strings.LastIndex(ref, "/")+1:]], defs)
	}
	out := map[string]any{}
	for _, key := range schemaShapeKeys {
		value, present := s[key]
		if !present {
			continue
		}
		switch key {
		case "type":
			if list, ok := value.([]any); ok {
				var kept []string
				for _, t := range list {
					if t != "null" {
						kept = append(kept, t.(string))
					}
				}
				sort.Strings(kept)
				if len(kept) == 1 {
					value = kept[0]
				} else {
					value = kept
				}
			}
		case "properties":
			props := map[string]any{}
			for k, v := range value.(map[string]any) {
				props[k] = normaliseSchema(v, defs)
			}
			value = props
		case "items":
			value = normaliseSchema(value, defs)
		case "additionalProperties":
			if _, isSchema := value.(map[string]any); !isSchema {
				continue
			}
			value = normaliseSchema(value, defs)
		case "required":
			var names []string
			for _, n := range value.([]any) {
				names = append(names, n.(string))
			}
			sort.Strings(names)
			value = names
		}
		out[key] = value
	}
	return out
}

// generic round-trips a value through JSON so it is plain maps, slices and scalars.
func generic(v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		panic(err)
	}
	return out
}

func nonEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func discover(ctx context.Context, cs *mcp.ClientSession) (any, error) {
	init := cs.InitializeResult()
	out := map[string]any{
		"server":             init.ServerInfo.Name,
		"protocol_version":   init.ProtocolVersion,
		"instructions":       nonEmpty(init.Instructions),
		"tools":              []string{},
		"resources":          []string{},
		"resource_templates": []string{},
		"prompts":            []string{},
	}
	var tools, resources, templates, prompts []string
	for t, err := range cs.Tools(ctx, nil) {
		if err != nil {
			return nil, err
		}
		tools = append(tools, t.Name)
	}
	for r, err := range cs.Resources(ctx, nil) {
		if err != nil {
			return nil, err
		}
		resources = append(resources, r.URI)
	}
	for r, err := range cs.ResourceTemplates(ctx, nil) {
		if err != nil {
			return nil, err
		}
		templates = append(templates, r.URITemplate)
	}
	for p, err := range cs.Prompts(ctx, nil) {
		if err != nil {
			return nil, err
		}
		prompts = append(prompts, p.Name)
	}
	for key, list := range map[string][]string{"tools": tools, "resources": resources,
		"resource_templates": templates, "prompts": prompts} {
		slices.Sort(list)
		if list != nil {
			out[key] = list
		}
	}
	return out, nil
}

func listTools(ctx context.Context, cs *mcp.ClientSession) (any, error) {
	out := []any{}
	for t, err := range cs.Tools(ctx, nil) {
		if err != nil {
			return nil, err
		}
		var annotations any
		if t.Annotations != nil {
			hints := generic(t.Annotations).(map[string]any)
			for _, k := range []string{"readOnlyHint", "idempotentHint"} {
				if hints[k] == false { // Go's struct cannot say "absent"; Python drops them too
					delete(hints, k)
				}
			}
			annotations = hints
		}
		out = append(out, map[string]any{
			"name":         t.Name,
			"title":        nonEmpty(t.Title),
			"description":  nonEmpty(t.Description),
			"annotations":  annotations,
			"inputSchema":  normaliseSchema(generic(t.InputSchema), nil),
			"outputSchema": normaliseSchema(generic(t.OutputSchema), nil),
		})
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].(map[string]any)["name"].(string) < out[j].(map[string]any)["name"].(string)
	})
	return out, nil
}

// ── 4. Calls, resources and prompts ─────────────────────────────────────────

// rpcMessage turns a JSON-RPC error into the {"error": message} shape.
func rpcMessage(err error) (any, error) {
	var je *jsonrpc.Error
	if errors.As(err, &je) {
		return map[string]any{"error": je.Message}, nil
	}
	return nil, err
}

const noText = "<no text>"

func call(ctx context.Context, cs *mcp.ClientSession, name string, arguments map[string]any) (any, error) {
	res, err := cs.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: arguments})
	if err != nil {
		return nil, err
	}
	if res.IsError {
		message := noText
		if len(res.Content) > 0 {
			if tc, ok := res.Content[0].(*mcp.TextContent); ok {
				message = tc.Text
			}
		}
		return map[string]any{"error": message}, nil
	}
	return map[string]any{"structured": generic(res.StructuredContent)}, nil
}

func read(ctx context.Context, cs *mcp.ClientSession, uri string) (any, error) {
	res, err := cs.ReadResource(ctx, &mcp.ReadResourceParams{URI: uri})
	if err != nil {
		return rpcMessage(err)
	}
	contents := []any{}
	for _, c := range res.Contents {
		entry := map[string]any{"uri": c.URI, "mimeType": c.MIMEType}
		var j any
		if err := json.Unmarshal([]byte(c.Text), &j); err != nil {
			entry["text"] = c.Text
		} else {
			entry["json"] = j
		}
		contents = append(contents, entry)
	}
	return map[string]any{"contents": contents}, nil
}

func prompt(ctx context.Context, cs *mcp.ClientSession, name string, arguments map[string]any) (any, error) {
	args := map[string]string{}
	keys := make([]string, 0, len(arguments))
	for k := range arguments {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		s, ok := arguments[k].(string)
		if !ok {
			return nil, fmt.Errorf("prompt arguments must be strings: %s", k)
		}
		args[k] = s
	}
	res, err := cs.GetPrompt(ctx, &mcp.GetPromptParams{Name: name, Arguments: args})
	if err != nil {
		return rpcMessage(err)
	}
	contents := []any{}
	for _, m := range res.Messages {
		var text any
		if tc, ok := m.Content.(*mcp.TextContent); ok {
			text = tc.Text
		}
		contents = append(contents, map[string]any{"role": string(m.Role), "text": text})
	}
	return map[string]any{"contents": contents}, nil
}

// ── 5. Answering input-required rounds ──────────────────────────────────────

// A server that needs a confirmation answers a call with an InputRequiredResult holding an
// elicitation request. The SDK drives the retry; this handler supplies the answer. The answer is
// the client's own assertion: the server cannot tell a human from a script (-yes).
type Answerer func(ctx context.Context, message string) bool

func always(value bool) Answerer {
	return func(context.Context, string) bool { return value }
}

// askTerminal prompts on stderr (stdout stays clean JSON) and reads the answer from stdin.
// Only y or yes accepts.
func askTerminal(in io.Reader, errOut io.Writer) Answerer {
	reader := bufio.NewReader(in)
	return func(_ context.Context, message string) bool {
		fmt.Fprintf(errOut, "%s [y/N] ", message)
		line, _ := reader.ReadString('\n')
		switch strings.ToLower(strings.TrimSpace(line)) {
		case "y", "yes":
			return true
		}
		return false
	}
}

func elicitationHandler(answer Answerer) func(context.Context, *mcp.ElicitRequest) (*mcp.ElicitResult, error) {
	return func(ctx context.Context, req *mcp.ElicitRequest) (*mcp.ElicitResult, error) {
		if !answer(ctx, req.Params.Message) {
			return &mcp.ElicitResult{Action: "decline"}, nil
		}
		// Accept: set every boolean field the form asks for to true. Any other field type is not
		// something a y/N prompt can fill, so it is declined rather than guessed.
		schema, _ := generic(req.Params.RequestedSchema).(map[string]any)
		props, _ := schema["properties"].(map[string]any)
		content := map[string]any{}
		for name, p := range props {
			if m, _ := p.(map[string]any); m["type"] != "boolean" {
				return &mcp.ElicitResult{Action: "decline"}, nil
			}
			content[name] = true
		}
		return &mcp.ElicitResult{Action: "accept", Content: content}, nil
	}
}

// ── 6. Entry point ──────────────────────────────────────────────────────────

// dump renders v as ASCII-only JSON with sorted keys (Go sorts map keys) and 2-space indent, the
// same bytes as Python's json.dumps(sort_keys=True, indent=2).
func dump(v any) string {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		panic(err)
	}
	var out strings.Builder
	for _, r := range strings.TrimRight(buf.String(), "\n") {
		switch {
		case r < 0x80:
			out.WriteRune(r)
		case r >= 0x10000:
			hi, lo := utf16.EncodeRune(r)
			fmt.Fprintf(&out, "\\u%04x\\u%04x", hi, lo)
		default:
			fmt.Fprintf(&out, "\\u%04x", r)
		}
	}
	return out.String()
}

func parseObject(args []string, i int) (map[string]any, error) {
	obj := map[string]any{}
	if len(args) > i {
		if err := json.Unmarshal([]byte(args[i]), &obj); err != nil || obj == nil {
			return nil, errors.New("arguments are not a JSON object")
		}
	}
	return obj, nil
}

func run(ctx context.Context, cs *mcp.ClientSession, command string, args []string) (any, error) {
	need := func(n int) error {
		if len(args) < n {
			return fmt.Errorf("%s needs %d argument(s)", command, n)
		}
		return nil
	}
	switch command {
	case "discover":
		return discover(ctx, cs)
	case "tools":
		return listTools(ctx, cs)
	case "call", "prompt":
		if err := need(1); err != nil {
			return nil, err
		}
		obj, err := parseObject(args, 1)
		if err != nil {
			return nil, err
		}
		if command == "call" {
			return call(ctx, cs, args[0], obj)
		}
		return prompt(ctx, cs, args[0], obj)
	case "read":
		if err := need(1); err != nil {
			return nil, err
		}
		return read(ctx, cs, args[0])
	}
	return nil, fmt.Errorf("unknown command: %s", command)
}

func main() {
	stdio := flag.Bool("stdio", false, "spawn read_server.py and use stdio")
	url := flag.String("url", "", "Streamable HTTP endpoint, e.g. http://127.0.0.1:8765/mcp")
	tokenFile := flag.String("token-file", "", "file holding the OAuth access token for -url")
	tokenFlag := flag.String("token", "", "OAuth access token for -url (visible to other users: prefer -token-file)")
	yes := flag.Bool("yes", false, "accept input-required rounds")
	no := flag.Bool("no", false, "decline input-required rounds")
	flag.Parse()
	fail := func(err error) {
		fmt.Fprintln(os.Stderr, "client:", err)
		os.Exit(1)
	}
	if flag.NArg() < 1 || *stdio == (*url != "") || (*yes && *no) {
		fmt.Fprintln(os.Stderr, "usage: client (-stdio | -url URL [-token-file PATH | -token TOKEN]) [-yes | -no] discover|tools|call NAME JSON|read URI|prompt NAME JSON")
		os.Exit(2)
	}
	answer := askTerminal(os.Stdin, os.Stderr)
	if *yes {
		answer = always(true)
	} else if *no {
		answer = always(false)
	}
	if *stdio && (*tokenFile != "" || *tokenFlag != "") {
		fail(errors.New("a token needs --url / -url"))
	}
	token := ""
	if *url != "" { // LUMEN_TOKEN is ambient: used for HTTP only
		t, warn, err := resolveToken(*tokenFile, *tokenFlag, os.Getenv("LUMEN_TOKEN"))
		if warn {
			fmt.Fprintln(os.Stderr, tokenFlagWarning)
		}
		if err != nil {
			fail(err)
		}
		token = t
	}
	transport, err := newTransport(*stdio, *url, token)
	if err != nil {
		fail(err)
	}
	ctx := context.Background()
	cs, err := connect(ctx, transport, answer)
	if err != nil {
		fail(err)
	}
	defer cs.Close()
	result, err := run(ctx, cs, flag.Arg(0), flag.Args()[1:])
	if err != nil {
		fail(err)
	}
	fmt.Println(dump(result))
}
