# AI Engineering

An interactive masterclass on AI engineering, with Python and Go side by side. Two tracks are live: **Agent Harness** (build the loop, tools, context handling and guardrails around a model) and **LLMs** (evaluate, judge and tune an LLM feature).

Hosted page: https://claude.ai/artifact/MH9ZBPk8GC1duTWc4Ke5cw (private until shared from the page's Share menu)

## Layout

- `harness/python/harness.py` and `harness/go/main.go` — the reference harness, one file per language with matching numbered sections.
- `llms/python/triage_lab.py` and `llms/go/main.go` — the LLMs track's eval lab (support-ticket triage), one file per language with matching numbered sections 1–10.
- `llms/finetune/lora.py` — the LoRA fine-tuning script (Python only; runs on a Colab GPU).
- `llms/data/` — tickets, prompts, reply labels and saved model responses; see [llms/data/README.md](llms/data/README.md).
- `app/` — the Astro site that teaches both tracks (lessons, diagrams, simulators, quizzes).

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

## Run the LLMs lab

Replay mode is the default: every model answer is read from `llms/data/responses/`, so these commands are free and need no API key.

```sh
pip install -r llms/python/requirements.txt

python3 llms/python/triage_lab.py run --prompt v2_rules --split dev
python3 llms/python/triage_lab.py compare --prompt v1_basic --vs v2_rules
python3 llms/python/triage_lab.py judge --prompt v2_rules --split test
python3 llms/python/triage_lab.py agreement
python3 llms/python/triage_lab.py gate --prompt v1_basic --baseline v2_rules --split dev   # FAIL, exit 1

cd llms/go && go run . run --prompt v2_rules     # same scores in Go
```

A request with no saved answer (a new prompt version, ticket, model or effort) stops with a replay-miss error; it never falls back to a paid call.

The data files (tickets, prompts, reply labels, saved responses), how the labels were made and what the recordings cost are described in [llms/data/README.md](llms/data/README.md).

### Test the lab

The tests use fixtures and fake models and never call the API.

```sh
cd llms/python && python3 -m pytest -q
cd llms/go && go test ./...
python3 -m pytest llms/finetune -q     # from the repo root; LoRA data formatting only, no GPU
```

### Record new answers (costs money)

`--mode record` (on `run` or `judge`) and the `record` command call the API and append to `llms/data/responses/`. Every paid run first prints the estimated cost, the worst case if every reply uses `max_tokens`, and the `--max-cost` cap (default $10), then asks for approval; nothing is called without a `y` or `--yes`. A run stops at the cap and keeps what finished; requests already recorded are skipped.

```sh
export ANTHROPIC_API_KEY=sk-ant-...
python3 llms/python/triage_lab.py run --prompt v2_rules --model claude-sonnet-5-5 --effort medium --mode record
python3 llms/python/triage_lab.py record --plan all --max-cost 10    # the full recording plan
```

### LoRA on Colab

`llms/finetune/lora.py` fine-tunes `Qwen/Qwen2.5-0.5B-Instruct` with LoRA on the dev tickets and grades it on test. It needs a GPU but no API key. In Google Colab (Runtime > Change runtime type > T4 GPU), about 10 minutes:

1. Zip the repo's `llms/` folder and upload `llms.zip` in Colab's file panel.
2. Run `!unzip -q llms.zip`, `!pip install -q -r llms/finetune/requirements.txt`, `!python llms/finetune/lora.py`. The first run downloads about 1 GB of model weights.
3. Download `predictions.jsonl` before the session ends, then re-score it anywhere with `python3 llms/finetune/lora.py --score predictions.jsonl`.

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

## Adding content

Every track and lesson follows [docs/STYLE.md](docs/STYLE.md): the lesson anatomy, writing rules, components, design tokens and code conventions. Start new work from a template:

```sh
cd app
npm run new:lesson -- <track> <slug> "<Title>"   # from templates/lesson.mdx
cp templates/track.yaml src/content/tracks/<id>.yaml
```

`npm test` enforces the checkable rules: section order, no leftover `TODO`, glossary terms, colors only from `src/styles/tokens.css`, and valid UTF-8 with no garbled characters in any source file.
