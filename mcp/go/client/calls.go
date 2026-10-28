// Lumen MCP client, part 3 of 3: calls, resources and prompts (section 1), and answering
// input-required rounds (2). The Python twin is mcp/python/client_calls.py.

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/jsonrpc"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// ── 1. Calls, resources and prompts ─────────────────────────────────────────

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

// ── 2. Answering input-required rounds ──────────────────────────────────────

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
