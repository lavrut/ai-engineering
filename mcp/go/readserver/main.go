// The entry point: lumen-read over stdio (default) or Streamable HTTP on 127.0.0.1.
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
		log.Printf("lumen-read listening on http://%s/mcp", addr)
		log.Fatal(http.ListenAndServe(addr, NewHTTPHandler(s)))
	}
	if err := s.Run(context.Background(), &mcp.StdioTransport{}); err != nil {
		log.Fatal(err)
	}
}
