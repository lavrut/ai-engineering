package core

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// ── 1. Writing ──────────────────────────────────────────────────────────────
// LabelRow is {ticket_id, label, source, client_id, model, confirmed, time}; model is null when unset.
type LabelRow struct {
	TicketID  string  `json:"ticket_id"`
	Label     string  `json:"label"`
	Source    string  `json:"source"`
	ClientID  string  `json:"client_id"`
	Model     *string `json:"model"`
	Confirmed bool    `json:"confirmed"`
	Time      string  `json:"time"`
}

type LabelInput struct {
	Path      string // "" means LabelsPath()
	Source    string // "agent" or "human"
	ClientID  string
	Model     *string
	Confirmed bool
}

// WriteLabel appends one row under an exclusive flock, so concurrent processes never
// interleave. The whole line goes out in a single write on an O_APPEND descriptor.
func WriteLabel(ticketID, label string, in LabelInput) (LabelRow, error) {
	ok := false
	for _, c := range Categories {
		ok = ok || c == label
	}
	if !ok {
		return LabelRow{}, fmt.Errorf("label must be one of: %s", strings.Join(Categories, ", "))
	}
	if in.Source != "agent" && in.Source != "human" {
		return LabelRow{}, fmt.Errorf("source must be agent or human")
	}
	row := LabelRow{ticketID, label, in.Source, in.ClientID, in.Model, in.Confirmed,
		time.Now().UTC().Format("2006-01-02T15:04:05+00:00")}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf) // Encode appends the newline
	enc.SetEscapeHTML(false)     // like Python's ensure_ascii=False: "<" stays "<"
	if err := enc.Encode(row); err != nil {
		return LabelRow{}, err
	}
	path := in.Path
	if path == "" {
		path = LabelsPath()
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return LabelRow{}, err
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return LabelRow{}, err
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		return LabelRow{}, err
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	if _, err := f.Write(buf.Bytes()); err != nil {
		return LabelRow{}, err
	}
	return row, nil
}

// ── 2. Eligibility ──────────────────────────────────────────────────────────
// EligibleForEval: only a human's confirmed label may enter an eval set.
func EligibleForEval(row LabelRow) bool { return row.Source == "human" && row.Confirmed }
