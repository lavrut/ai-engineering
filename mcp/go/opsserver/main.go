// The entry point: lumen-ops over Streamable HTTP on 127.0.0.1 only (no stdio, by design).
// Always an OAuth resource server (tickets:write, audience http://127.0.0.1:8766/mcp).
//
//	go run ./opsserver [-port 8766] [-labels PATH]
package main

import (
	"flag"
	"fmt"
	"log"
	"net/http"
)

// ── 6. Entry point ──────────────────────────────────────────────────────────
func main() {
	port := flag.Int("port", DefaultPort, "HTTP port")
	labels := flag.String("labels", "", "append-only label file (default mcp/data/labels.jsonl)")
	flag.Parse()
	addr := fmt.Sprintf("127.0.0.1:%d", *port)
	h, err := NewHTTPHandler(NewOpsServer(*labels), *port)
	if err != nil {
		log.Fatalf("opsserver: %v", err) // no key file: never fall back to a default key
	}
	log.Printf("lumen-ops listening on http://%s/mcp", addr)
	log.Fatal(http.ListenAndServe(addr, h))
}
