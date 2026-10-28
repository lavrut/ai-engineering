// handgo runs one ticket through one hand-built Go pattern or desk, with the real Model (the proxy) and the real
// MCP Tools, and prints the run as one JSON object on stdout. agents/run.py's hand-go engine builds and runs it;
// it is not meant to be run by hand.
//
//	handgo -desk pattern:chain|pattern:route|pattern:parallel|pattern:orchestrate|pattern:evaluate_optimize|single|supervisor
//	       [-proxy URL] [-python PATH] [-approve accept|decline] [-elicit accept|decline] [-labels PATH]  < ticket.json
//
// stdout: {"output", "calls", "trace"} (trace: the R2 records in order), or {"error", "proxy_status"} with exit 2
// when the proxy refused (a replay miss or the cost cap) and exit 1 for anything else.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	patterns "lumen-agents"
	"lumen-agents/live"
)

// ── 1. Configuration ────────────────────────────────────────────────────────

// toolAgent is the Trace agent of each desk's tool records (desk_core.open_desk_tools' `agent`).
var toolAgent = map[string]string{"pattern:parallel": "parallel", "pattern:orchestrate": "orchestrator",
	"single": "single", "supervisor": "supervisor"}

func servers(desk string) []string {
	switch desk {
	case "pattern:parallel", "pattern:orchestrate":
		return []string{"read"}
	case "single", "supervisor":
		return []string{"read", "ops"}
	}
	return nil
}

// ── 2. One run ──────────────────────────────────────────────────────────────

type result struct {
	Output any                    `json:"output"`
	Calls  any                    `json:"calls"`
	Trace  []patterns.TraceRecord `json:"trace"`
}

func run(desk string, t patterns.Ticket, cfg live.ToolsConfig, proxy string) (result, error) {
	pr, err := patterns.LoadPrompts(patterns.DefaultPromptsPath())
	if err != nil {
		return result{}, err
	}
	log := &live.TraceLog{}
	var tools *live.Tools
	schemas := map[string]map[string]any{}
	if s := servers(desk); s != nil {
		cfg.Servers, cfg.Agent, cfg.Log = s, toolAgent[desk], log
		if tools, err = live.OpenTools(cfg); err != nil {
			return result{}, err
		}
		defer tools.Close()
		schemas = tools.Schemas()
	}
	m := live.NewModel(proxy, schemas, log)
	var out any
	var calls any
	switch desk {
	case "pattern:chain", "pattern:route", "pattern:parallel", "pattern:orchestrate", "pattern:evaluate_optimize":
		var r patterns.Run
		switch desk {
		case "pattern:chain":
			r, err = patterns.Chain(t, m, pr)
		case "pattern:route":
			r, err = patterns.Route(t, m)
		case "pattern:parallel":
			r, err = patterns.Parallel(t, m, tools, 1) // workers=1: a fixed trace order (agents/run.py)
		case "pattern:orchestrate":
			r, err = patterns.Orchestrate(t, m, tools)
		default:
			r, err = patterns.EvaluateOptimize(t, patterns.MaxRounds, m, pr)
		}
		out, calls = r.Output, r.Calls
	case "single", "supervisor":
		var r patterns.DeskRun
		if desk == "single" {
			r, err = patterns.SingleAgent(t, m, tools)
		} else {
			r, err = patterns.Supervisor(t, m, tools, pr)
		}
		out, calls = r.Output, r.Calls
	default:
		return result{}, fmt.Errorf("unknown desk %q", desk)
	}
	if err != nil {
		return result{}, err
	}
	return result{out, calls, log.Records()}, nil
}

// ── 3. Entry point ──────────────────────────────────────────────────────────

func main() {
	desk := flag.String("desk", "", "pattern:<name>, single or supervisor")
	proxy := flag.String("proxy", live.ProxyURL, "the record/replay proxy")
	python := flag.String("python", filepath.Join(patterns.Root(), ".venv-agents", "bin", "python"), "runs the MCP servers")
	approve := flag.String("approve", "decline", "the scripted person the guard asks")
	elicit := flag.String("elicit", "accept", "the scripted answer to lumen-ops' confirmation")
	labels := flag.String("labels", "", "lumen-ops' label file (required for single and supervisor)")
	flag.Parse()
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	fail := func(code int, err error, status int) {
		_ = enc.Encode(map[string]any{"error": err.Error(), "proxy_status": status})
		os.Exit(code)
	}
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		fail(1, err, 0)
	}
	var t patterns.Ticket
	if err := json.Unmarshal(raw, &t); err != nil || t.ID == "" {
		fail(1, fmt.Errorf("stdin must be a ticket {id, text}: %v", err), 0)
	}
	if (*desk == "single" || *desk == "supervisor") && strings.TrimSpace(*labels) == "" {
		fail(1, errors.New("-labels is required for the desks"), 0)
	}
	r, err := run(*desk, t, live.ToolsConfig{Python: *python, Root: patterns.Root(), Approve: *approve, Elicit: *elicit, Labels: *labels}, *proxy)
	var pe *live.ProxyError
	if errors.As(err, &pe) {
		fail(2, err, pe.Status)
	} else if err != nil {
		fail(1, err, 0)
	}
	if err := enc.Encode(r); err != nil {
		fail(1, err, 0)
	}
}
