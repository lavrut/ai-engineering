# AI Engineering

An interactive masterclass on building AI agent harnesses, with Python and Go side by side.

Hosted page: (link added after publishing)

## Layout

- `harness/python/harness.py` and `harness/go/main.go` — the reference harness, one file per language with matching numbered sections.
- `app/` — the Astro site that teaches it (lessons, diagrams, simulator, quizzes).

## Run a harness

Both harnesses call the Claude API, so running one costs money. They work inside `./workspace` (created if missing) and append events to `./trace.jsonl`.

```sh
export ANTHROPIC_API_KEY=sk-ant-...

# Python
cd harness/python
pip install -r requirements.txt
python harness.py

# Go
cd harness/go
go run .
```

Type a task at the prompt, or `exit`.

## Test the harnesses

The tests use a fake model and never call the API.

```sh
cd harness/python && pytest
cd harness/go && go test ./...
```

## The site

```sh
cd app
npm install
npm run dev        # dev server at http://localhost:4321
npm test           # vitest
npm run build      # astro check + test type-check + static build into dist/
npm run package    # dist/ -> publish/ in the page-host format
```

`npm run package` writes `publish/index.html` without a document skeleton (the host supplies one), makes every `_astro/` reference relative, and exits 1 listing any external URL outside the allowed hosts (`fonts.googleapis.com`, `fonts.gstatic.com`, `cdnjs.cloudflare.com`, `cdn.jsdelivr.net`, `unpkg.com`).
