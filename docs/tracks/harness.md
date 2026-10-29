# Agent Harness: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

## Run a harness

Both harnesses call the Claude API, so every task you type costs money. Before the first call of each task the REPL prints an estimate (typical, and a worst case that assumes every call stays at the context budget; it is not a ceiling) and asks `Run it? [y/N]`; anything but `y` skips the task, and with no terminal to ask the answer is no. `--max-cost` (default $10) is the hard stop: it is checked before every model call against what the session has spent plus the most that call could cost (an estimate, so the session stops at about the cap). `--yes` approves every task without asking: use it only for a scripted run you have already approved. They work inside `./workspace` (created if missing) and append events to `./trace.jsonl`.

The key is read from `ANTHROPIC_API_KEY` only and is never printed or logged. Set it without putting it in your shell history:

```sh
read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
```

Python (from the repo root; the venv once: `python3 -m venv .venv-harness && .venv-harness/bin/pip install -r harness/python/requirements.txt`):

```sh
cd harness/python && ../../.venv-harness/bin/python harness.py --max-cost 10
```

Go (from the repo root):

```sh
cd harness/go && go run . --max-cost 10
```

Type a task at the prompt, or `exit`.

## Test the harnesses

The tests use a fake model (and, for the model call itself, a fake API server on 127.0.0.1 with a dummy key) and never call the API.

Set up the venv once, from the repo root (`.venv-harness/` is git-ignored; never install into your base Python):

```sh
python3 -m venv .venv-harness && .venv-harness/bin/pip install -r harness/python/requirements.txt
.venv-harness/bin/python -m pytest harness/python -q
(cd harness/go && go test ./...)
```
