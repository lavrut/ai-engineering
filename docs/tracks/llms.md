# LLMs: running and testing the track

Moved from the repository README on 2026-10-29; the commands are unchanged. Run everything from the repository root.

## Run the LLMs lab

Replay mode is the default: every model answer is read from `llms/data/responses/`, so these commands are free and need no API key.

```sh
python3 -m venv .venv-llms && .venv-llms/bin/pip install -r llms/python/requirements.txt    # once, from the repo root; .venv-llms/ is git-ignored

.venv-llms/bin/python llms/python/triage_lab.py run --prompt v2_rules --split dev
.venv-llms/bin/python llms/python/triage_lab.py compare --prompt v1_basic --vs v2_rules
.venv-llms/bin/python llms/python/triage_lab.py judge --prompt v2_rules --split test
.venv-llms/bin/python llms/python/triage_lab.py agreement
.venv-llms/bin/python llms/python/triage_lab.py gate --prompt v1_basic --baseline v2_rules --split dev   # FAIL, exit 1

cd llms/go && go run . run --prompt v2_rules     # same scores in Go
```

A request with no saved answer (a new prompt version, ticket, model or effort) stops with a replay-miss error; it never falls back to a paid call.

The data files (tickets, prompts, reply labels, saved responses), how the labels were made and what the recordings cost are described in [llms/data/README.md](llms/data/README.md).

### Test the lab

The tests use fixtures and fake models and never call the API.

```sh
.venv-llms/bin/python -m pytest llms/python -q
cd llms/go && go test ./...
.venv-llms/bin/python -m pytest llms/finetune -q     # LoRA data formatting only, no GPU
```

### Record new answers (costs money)

`--mode record` (on `run` or `judge`) and the `record` command call the API and append to `llms/data/responses/`. Every paid run first prints the estimated cost, the worst case if every reply uses `max_tokens`, and the `--max-cost` cap (default $10), then asks for approval; nothing is called without a `y` or `--yes`. A run stops at the cap and keeps what finished; requests already recorded are skipped.

```sh
read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY    # paste the key; it is not echoed or kept in shell history
.venv-llms/bin/python llms/python/triage_lab.py run --prompt v2_rules --model claude-sonnet-5-5 --effort medium --mode record
.venv-llms/bin/python llms/python/triage_lab.py record --plan all --max-cost 10    # the full recording plan
```

### The same eval with Inspect (optional)

Lesson 11 re-runs the triage eval through [Inspect AI](https://inspect.aisi.org.uk/). It replays the same saved responses, so it is free and needs no API key. Install Inspect in its own venv, never in your base Python (`.venv-inspect/` is git-ignored):

```sh
python3 -m venv .venv-inspect
.venv-inspect/bin/pip install -r llms/inspect/requirements.txt    # inspect-ai==0.3.285
.venv-inspect/bin/pip install -e llms/inspect                     # registers the replay provider, which the CLI needs

.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v1_basic --log-dir llms/inspect/logs
.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v2_rules --epochs 3 --log-dir llms/inspect/logs
.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v3_examples --log-dir llms/inspect/logs
.venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage_judged --model replay/claude-haiku-5-5 --model-role grader=replay/claude-opus-5-5 --log-dir llms/inspect/logs

.venv-inspect/bin/inspect view --log-dir llms/inspect/logs        # browse the logs
.venv-inspect/bin/python llms/inspect/summarize.py                # run all four, write llms/data/inspect_summary.json
.venv-inspect/bin/python -m pytest llms/inspect/tests -q          # the parity tests
```

`summarize.py` exits non-zero if an Inspect score stops matching the lab's. Logs go to `llms/inspect/logs/`; only the summary is committed.

**The live path costs money.** Any model that is not `replay/...`, as `--model` or as the grader role (`anthropic/claude-haiku-5-5`, say), calls the API and pays for it. Inspect has no estimate and no y/N prompt, so the task refuses a non-replay model unless `LUMEN_LIVE_OK=1` is set. Price the run first; this is free and calls nothing:

```sh
.venv-inspect/bin/python llms/inspect/summarize.py --estimate     # add --model / --grader anthropic/<model> to price another model
```

It prints calls × the lab's `PRICES` per run: the `v2_rules` run over 3 epochs is 180 Haiku calls, about $0.08 (worst case $0.20), and the judged run's Opus grader adds about $0.47 (worst case $0.91). If you accept the cost, run that one command with the approval in front (`LUMEN_LIVE_OK=1 .venv-inspect/bin/inspect eval ... --model anthropic/claude-haiku-5-5 ...`). There is no run-wide spend cap: Inspect's `--cost-limit` applies to each sample.

### LoRA on Colab

`llms/finetune/lora.py` fine-tunes `Qwen/Qwen2.5-0.5B-Instruct` with LoRA on the dev tickets and grades it on test. It needs a GPU but no API key. In Google Colab (Runtime > Change runtime type > T4 GPU), about 10 minutes:

1. Zip the repo's `llms/` folder and upload `llms.zip` in Colab's file panel.
2. Run `!unzip -q llms.zip`, `!pip install -q -r llms/finetune/requirements.txt`, `!python llms/finetune/lora.py`. The first run downloads about 1 GB of model weights.
3. Download `predictions.jsonl` before the session ends, then re-score it anywhere with `.venv-llms/bin/python llms/finetune/lora.py --score predictions.jsonl`.
