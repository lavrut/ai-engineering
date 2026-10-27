// The entry point: lumen-read over stdio (default) or Streamable HTTP on 127.0.0.1.
// Over HTTP it is an OAuth resource server (tickets:read, audience http://127.0.0.1:8765/mcp).
//
//	go run ./readserver [-http] [-port 8765]
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// ── 6. Entry point ──────────────────────────────────────────────────────────
func main() {
	useHTTP := flag.Bool("http", false, "serve Streamable HTTP on 127.0.0.1 (default: stdio)")
	port := flag.Int("port", 8765, "HTTP port")
	flag.Parse()
	s := NewReadServer()
	if *useHTTP {
		addr := fmt.Sprintf("127.0.0.1:%d", *port)
		h, err := NewHTTPHandler(s)
		if err != nil {
			log.Fatalf("readserver: %v", err) // no key file: never fall back to a default key
		}
		log.Printf("lumen-read listening on http://%s/mcp", addr)
		log.Fatal(http.ListenAndServe(addr, h))
	}
	if err := s.Run(context.Background(), &mcp.StdioTransport{}); err != nil {
		log.Fatal(err)
	}
}
